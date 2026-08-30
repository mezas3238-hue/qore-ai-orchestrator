from __future__ import annotations

import base64
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import resume_after_agent_completion as resume


class AgentCompletionResumeTests(unittest.TestCase):
    def completion(self, package: str = "QORE-CODEX-aaaaaaaaaaaa-0123456789abcdef"):
        return {
            "actor": "CODEX",
            "repo": resume.ORCH_REPO,
            "run_id": 100,
            "run_attempt": 1,
            "package_id": package,
            "parent_architect_run_id": 77,
            "source_main_sha": "a" * 40,
            "conclusion": "success",
            "agent_cost_usd": Decimal("0.200000"),
            "agent_cost_kind": "observed",
        }

    def prior_receipt(
        self,
        *,
        cycle_index: int = 1,
        spend: str = "0.500000",
        packages: list[str] | None = None,
        sol_calls_used: int = 1,
        codex_jobs_used: int = 0,
    ) -> dict[str, object]:
        return {
            "event_key": "CODEX:x:1:1",
            "session_id": "QORE-ORCH-R77",
            "cycle_index": cycle_index,
            "estimated_spend_usd": spend,
            "package_history": packages or ["QORE-CODEX-bbbbbbbbbbbb-0123456789abcdef"],
            "sol_calls_used": sol_calls_used,
            "codex_jobs_used": codex_jobs_used,
            "dispatched": True,
            "child_architect_run_id": 77,
        }

    def test_sol_pricing_counts_cached_and_cache_write_separately(self):
        usage = {
            "model": "gpt-5.6-sol",
            "input_tokens": 100_000,
            "cached_tokens": 20_000,
            "cache_write_tokens": 10_000,
            "output_tokens": 5_000,
        }
        # 70k * 4 + 20k * .4 + 10k * 5 + 5k * 20 = $0.438
        self.assertEqual(resume.estimate_usage_cost(usage), Decimal("0.438000"))

    def test_codex_pricing_is_current_pinned_rate(self):
        usage = {
            "model": "gpt-5.3-codex",
            "input_tokens": 100_000,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 10_000,
        }
        self.assertEqual(resume.estimate_usage_cost(usage), Decimal("0.315000"))

    def test_usage_rejects_token_laundering(self):
        usage = {
            "model": "gpt-5.6-sol",
            "input_tokens": 10,
            "cached_tokens": 8,
            "cache_write_tokens": 8,
            "output_tokens": 0,
        }
        with self.assertRaisesRegex(resume.ResumeError, "exceed input"):
            resume.estimate_usage_cost(usage)

    def test_unpriced_model_fails_closed(self):
        with self.assertRaisesRegex(resume.ResumeError, "unpriced model"):
            resume.estimate_usage_cost({"model": "unknown", "input_tokens": 0, "output_tokens": 0})

    def test_wrapped_github_content_decodes_strictly(self):
        payload = {"package_id": "QORE-SOL-aaaaaaaaaaaa-CLAUDE-R77"}
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        wrapped = encoded[:8] + "\n" + encoded[8:16] + "\n" + encoded[16:]
        self.assertEqual(resume._decode_content({"content": wrapped}), payload)
        with self.assertRaisesRegex(resume.ResumeError, "content is invalid"):
            resume._decode_content({"content": "%%%%"})

    def test_reviewer_title_is_exact_and_actor_specific(self):
        claude = "QORE-SOL-aaaaaaaaaaaa-CLAUDE-R77"
        deepseek = "QORE-SOL-aaaaaaaaaaaa-DS-EXPERT-R77"
        self.assertEqual(
            resume.reviewer_package_from_title(
                f"Claude QORE review · {claude}", "Claude QORE review", "CLAUDE_CODE"
            ),
            claude,
        )
        self.assertEqual(
            resume.reviewer_package_from_title(
                f"DeepSeek QORE review · {deepseek}", "DeepSeek QORE review", "DEEPSEEK"
            ),
            deepseek,
        )
        with self.assertRaisesRegex(resume.ResumeError, "exact package"):
            resume.reviewer_package_from_title(
                f"Claude QORE review · {deepseek}", "Claude QORE review", "CLAUDE_CODE"
            )

    def test_exact_event_is_deduplicated_before_dispatch(self):
        completion = self.completion()
        key = resume.event_key("CODEX", resume.ORCH_REPO, 100, 1)
        receipts = [
            {
                "event_key": key,
                "session_id": "QORE-ORCH-R77",
                "cycle_index": 1,
                "estimated_spend_usd": "0.500000",
                "sol_calls_used": 1,
                "codex_jobs_used": 1,
                "dispatched": True,
                "child_architect_run_id": 88,
            }
        ]
        receipt = resume.build_receipt(
            completion,
            receipts,
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertFalse(receipt["dispatched"])
        self.assertEqual(receipt["stop_reason"], "EXACT_COMPLETION_EVENT_ALREADY_RECEIPTED")

    def test_first_completion_starts_session_and_can_resume(self):
        receipt = resume.build_receipt(
            self.completion(),
            [],
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["session_id"], "QORE-ORCH-R77")
        self.assertEqual(receipt["cycle_index"], 1)
        self.assertEqual(receipt["estimated_spend_usd"], "0.500000")
        self.assertEqual(receipt["sol_calls_used"], 1)
        self.assertEqual(receipt["codex_jobs_used"], 1)
        self.assertIsNone(receipt["stop_reason"])

    def test_cycle_cap_stops_before_new_paid_sol_run(self):
        receipt = resume.build_receipt(
            self.completion(),
            [self.prior_receipt(cycle_index=3)],
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["stop_reason"], "AUTO_RESUME_CYCLE_CAP_REACHED")

    def test_estimated_spend_cap_stops_before_new_paid_sol_run(self):
        receipt = resume.build_receipt(
            self.completion(),
            [self.prior_receipt(spend="4.600000")],
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["estimated_spend_usd"], "5.100000")
        self.assertEqual(receipt["stop_reason"], "ESTIMATED_SPEND_CAP_REACHED")

    def test_sol_call_cap_reserves_full_next_architect_run(self):
        receipt = resume.build_receipt(
            self.completion(),
            [self.prior_receipt(sol_calls_used=8)],
            Decimal("0.100000"),
            [],
            mode="execute",
            max_auto_resumes=8,
            max_spend=Decimal("20.00"),
            architect_sol_calls=2,
            max_sol_calls=12,
            max_codex_jobs=8,
        )
        self.assertEqual(receipt["sol_calls_used"], 10)
        self.assertEqual(receipt["stop_reason"], "SOL_CALL_CAP_REACHED")

    def test_codex_job_cap_stops_before_another_architect_run(self):
        receipt = resume.build_receipt(
            self.completion(),
            [self.prior_receipt(codex_jobs_used=2)],
            Decimal("0.100000"),
            [],
            mode="execute",
            max_auto_resumes=8,
            max_spend=Decimal("20.00"),
            max_sol_calls=30,
            max_codex_jobs=3,
        )
        self.assertEqual(receipt["codex_jobs_used"], 3)
        self.assertEqual(receipt["stop_reason"], "CODEX_JOB_CAP_REACHED")

    def test_prior_lineage_without_explicit_counters_fails_closed(self):
        prior = self.prior_receipt()
        prior.pop("sol_calls_used")
        with self.assertRaisesRegex(resume.ResumeError, "sol_calls_used"):
            resume.build_receipt(
                self.completion(),
                [prior],
                Decimal("0.100000"),
                [],
                mode="execute",
                max_auto_resumes=3,
                max_spend=Decimal("5.00"),
            )

    def test_repeated_package_is_loop_signature(self):
        package = "QORE-CODEX-aaaaaaaaaaaa-0123456789abcdef"
        prior = self.prior_receipt(packages=[package])
        receipt = resume.build_receipt(
            self.completion(package),
            [prior],
            Decimal("0.100000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["stop_reason"], "LOOP_SIGNATURE_REPEATED_PACKAGE")

    def test_reviewer_package_extracts_exact_parent_architect_run(self):
        self.assertEqual(resume.reviewer_parent_run("QORE-SOL-aaaaaaaaaaaa-DS-EXPERT-R12345"), 12345)
        self.assertEqual(resume.reviewer_parent_run("QORE-SOL-aaaaaaaaaaaa-CLAUDE-R9"), 9)
        with self.assertRaises(resume.ResumeError):
            resume.reviewer_parent_run("QORE-SOL-aaaaaaaaaaaa-DS-EXPERT-R0")

    def test_multiple_lineage_receipts_fail_closed(self):
        receipts = [
            {"child_architect_run_id": 77, "dispatched": True},
            {"child_architect_run_id": 77, "dispatched": True},
        ]
        with self.assertRaisesRegex(resume.ResumeError, "multiple resume receipts"):
            resume.lineage_for_parent(receipts, 77)

    def test_manual_dry_run_never_authorizes_dispatch(self):
        receipt = resume.build_receipt(
            self.completion(),
            [],
            Decimal("0.100000"),
            [],
            mode="dry_run",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["stop_reason"], "DRY_RUN_ONLY")
        self.assertFalse(receipt["dispatched"])


if __name__ == "__main__":
    unittest.main()
