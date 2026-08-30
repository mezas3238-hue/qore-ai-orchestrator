from __future__ import annotations

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

    def test_exact_event_is_deduplicated_before_dispatch(self):
        completion = self.completion()
        key = resume.event_key("CODEX", resume.ORCH_REPO, 100, 1)
        receipts = [
            {
                "event_key": key,
                "session_id": "QORE-ORCH-R77",
                "cycle_index": 1,
                "estimated_spend_usd": "0.500000",
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
        self.assertIsNone(receipt["stop_reason"])

    def test_cycle_cap_stops_before_new_paid_sol_run(self):
        prior = {
            "event_key": "CODEX:x:1:1",
            "session_id": "QORE-ORCH-R77",
            "cycle_index": 3,
            "estimated_spend_usd": "1.000000",
            "package_history": ["QORE-CODEX-bbbbbbbbbbbb-0123456789abcdef"],
            "dispatched": True,
            "child_architect_run_id": 77,
        }
        receipt = resume.build_receipt(
            self.completion(),
            [prior],
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["stop_reason"], "AUTO_RESUME_CYCLE_CAP_REACHED")

    def test_estimated_spend_cap_stops_before_new_paid_sol_run(self):
        prior = {
            "event_key": "CODEX:x:1:1",
            "session_id": "QORE-ORCH-R77",
            "cycle_index": 1,
            "estimated_spend_usd": "4.600000",
            "package_history": ["QORE-CODEX-bbbbbbbbbbbb-0123456789abcdef"],
            "dispatched": True,
            "child_architect_run_id": 77,
        }
        receipt = resume.build_receipt(
            self.completion(),
            [prior],
            Decimal("0.300000"),
            [],
            mode="execute",
            max_auto_resumes=3,
            max_spend=Decimal("5.00"),
        )
        self.assertEqual(receipt["estimated_spend_usd"], "5.100000")
        self.assertEqual(receipt["stop_reason"], "ESTIMATED_SPEND_CAP_REACHED")

    def test_repeated_package_is_loop_signature(self):
        package = "QORE-CODEX-aaaaaaaaaaaa-0123456789abcdef"
        prior = {
            "event_key": "CODEX:x:1:1",
            "session_id": "QORE-ORCH-R77",
            "cycle_index": 1,
            "estimated_spend_usd": "0.500000",
            "package_history": [package],
            "dispatched": True,
            "child_architect_run_id": 77,
        }
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
