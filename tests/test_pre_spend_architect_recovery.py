from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import recover_pre_spend_architect as recovery  # noqa: E402
import resume_after_agent_completion as base  # noqa: E402


class PreSpendArchitectRecoveryTests(unittest.TestCase):
    def _archive(self, *names: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name in names:
                bundle.writestr(name, "{}\n")
        return buffer.getvalue()

    def _run(self, run_id: int = 55) -> dict[str, object]:
        return {
            "id": run_id,
            "name": "QORE Architect autonomous V2",
            "path": ".github/workflows/qore-architect-autonomous-v2.yml",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "run_attempt": 1,
        }

    def _jobs(self, *, executed_post_gate_step: str | None = None) -> dict[str, object]:
        steps: list[dict[str, object]] = []
        for name in recovery.REQUIRED_SUCCESS_STEPS:
            steps.append({"name": name, "conclusion": "success"})
        steps.append({"name": recovery.PRE_SPEND_GATE_STEP, "conclusion": "failure"})
        for name in recovery.REQUIRED_SKIPPED_STEPS:
            conclusion = "success" if name == executed_post_gate_step else "skipped"
            steps.append({"name": name, "conclusion": conclusion})
        return {"jobs": [{"name": "architect-cycle", "conclusion": "failure", "steps": steps}]}

    def _prior(self, *, child: int = 55, pre_spend_count: int = 0) -> dict[str, object]:
        return {
            "schema_version": "qore.orchestration.resume.receipt.v1",
            "event_key": "DEEPSEEK:repo:100:1",
            "actor": "DEEPSEEK",
            "package_id": "QORE-SOL-012345abcdef-DS-EXPERT-R123",
            "parent_architect_run_id": 123,
            "session_id": "QORE-ORCH-R123",
            "cycle_index": 1,
            "max_auto_resumes": 3,
            "estimated_spend_usd": "1.250000",
            "max_estimated_spend_usd": "5.00",
            "sol_calls_used": 2,
            "max_sol_calls": 12,
            "sol_calls_reserved_per_architect_run": 3,
            "codex_jobs_used": 0,
            "max_codex_jobs": 3,
            "package_history": ["QORE-SOL-012345abcdef-DS-EXPERT-R123"],
            "pre_spend_recovery_count": pre_spend_count,
            "dispatched": True,
            "child_architect_run_id": child,
            "stop_reason": None,
            "production_authority": False,
        }

    def test_exact_pre_spend_failure_is_recoverable(self) -> None:
        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            if path == "/actions/runs/55":
                return self._run()
            if path == "/actions/runs/55/jobs?filter=latest&per_page=100":
                return self._jobs()
            raise AssertionError(path)

        archive = self._archive(
            "qore-state.json",
            "external-reviewer-state.json",
            "external-reviewer-state-base.json",
            "codex-worker-state.json",
        )
        with patch.object(base, "api_json", side_effect=fake_api), patch.object(
            base, "artifact_bytes", return_value=archive
        ):
            observed = recovery.validate_pre_spend_failure("token", 55, 1, "a" * 40)

        self.assertEqual(observed["id"], 55)

    def test_model_usage_artifact_blocks_recovery(self) -> None:
        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            if path == "/actions/runs/55":
                return self._run()
            if path == "/actions/runs/55/jobs?filter=latest&per_page=100":
                return self._jobs()
            raise AssertionError(path)

        archive = self._archive("qore-state.json", "sol-usage-initial.json")
        with patch.object(base, "api_json", side_effect=fake_api), patch.object(
            base, "artifact_bytes", return_value=archive
        ):
            with self.assertRaisesRegex(base.ResumeError, "side-effect evidence"):
                recovery.validate_pre_spend_failure("token", 55, 1, "a" * 40)

    def test_any_post_gate_execution_blocks_recovery(self) -> None:
        bad_step = "Run GPT-5.6 Sol Principal Architect initial pass"

        def fake_api(_token: str, _api: str, path: str, **_kwargs: object) -> object:
            if path == "/actions/runs/55":
                return self._run()
            if path == "/actions/runs/55/jobs?filter=latest&per_page=100":
                return self._jobs(executed_post_gate_step=bad_step)
            raise AssertionError(path)

        with patch.object(base, "api_json", side_effect=fake_api):
            with self.assertRaisesRegex(base.ResumeError, "post-gate step was not skipped"):
                recovery.validate_pre_spend_failure("token", 55, 1, "a" * 40)

    def test_lineage_copy_does_not_reset_budget_or_cycle(self) -> None:
        prior = self._prior()
        receipt = recovery._copy_lineage(prior, 55, 1)

        self.assertEqual(receipt["session_id"], prior["session_id"])
        self.assertEqual(receipt["cycle_index"], prior["cycle_index"])
        self.assertEqual(receipt["estimated_spend_usd"], prior["estimated_spend_usd"])
        self.assertEqual(receipt["sol_calls_used"], prior["sol_calls_used"])
        self.assertEqual(receipt["codex_jobs_used"], prior["codex_jobs_used"])
        self.assertEqual(receipt["package_history"], prior["package_history"])
        self.assertEqual(receipt["pre_spend_recovery_count"], 1)
        self.assertTrue(receipt["verified_no_model_or_agent_side_effect"])

    def test_same_failed_run_cannot_dispatch_twice(self) -> None:
        prior = self._prior()
        existing = recovery._copy_lineage(prior, 55, 1)
        existing["dispatched"] = True
        existing["child_architect_run_id"] = 66
        receipts = [prior, existing]

        with patch.object(base, "dispatch_architect") as dispatch:
            receipt = recovery.recover("token", receipts, 55, 1, "a" * 40)

        dispatch.assert_not_called()
        self.assertFalse(receipt["dispatched"])
        self.assertEqual(receipt["stop_reason"], "PRE_SPEND_RECOVERY_ALREADY_DISPATCHED")
        self.assertEqual(receipt["existing_child_architect_run_id"], 66)

    def test_session_allows_at_most_one_pre_spend_recovery(self) -> None:
        prior = self._prior(pre_spend_count=1)
        with patch.object(base, "dispatch_architect") as dispatch:
            receipt = recovery.recover("token", [prior], 55, 1, "a" * 40)

        dispatch.assert_not_called()
        self.assertFalse(receipt["dispatched"])
        self.assertEqual(receipt["stop_reason"], "PRE_SPEND_RECOVERY_CAP_REACHED")

    def test_explicit_source_resume_must_match_live_lineage(self) -> None:
        prior = self._prior()
        wrong = dict(prior)
        wrong["cycle_index"] = 99
        with patch.object(base, "_receipt_for_run", return_value=wrong):
            with self.assertRaisesRegex(base.ResumeError, "does not match live lineage"):
                recovery.recover(
                    "token",
                    [prior],
                    55,
                    1,
                    "a" * 40,
                    source_resume_run_id=999,
                )


if __name__ == "__main__":
    unittest.main()
