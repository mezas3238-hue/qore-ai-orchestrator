from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import recover_post_spend_architect as recovery
import resume_after_agent_completion as base


class PostSpendArchitectRecoveryTests(unittest.TestCase):
    def test_receipt_preserves_new_session_and_counts_spent_sol_call(self):
        receipt = recovery._receipt(33338976459, 33338967360, Decimal("1.250000"), ["legacy"])
        self.assertEqual(receipt["session_id"], "QORE-ORCH-R33338976459")
        self.assertEqual(receipt["cycle_index"], 0)
        self.assertEqual(receipt["sol_calls_used"], 1)
        self.assertEqual(receipt["codex_jobs_used"], 0)
        self.assertEqual(receipt["estimated_spend_usd"], "1.250000")
        self.assertFalse(receipt["production_authority"])
        self.assertFalse(receipt["dispatched"])

    def test_existing_recovery_deduplicates_exact_failed_run(self):
        receipts = [
            {
                "actor": "CONTROLLER_POST_SPEND",
                "recovery_of_child_architect_run_id": 42,
                "dispatched": True,
                "child_architect_run_id": 99,
            }
        ]
        self.assertEqual(recovery._existing_recovery(receipts, 42)["child_architect_run_id"], 99)
        self.assertIsNone(recovery._existing_recovery(receipts, 43))

    def test_active_work_blocks_dispatch(self):
        policy_receipt = {
            "schema_version": "qore.orchestration.rearm.receipt.v1",
            "production_authority": False,
            "dispatched": True,
            "new_session_seed_architect_run_id": 42,
            "rearm_workflow_run_id": 7,
            "new_session_policy": {
                "max_auto_resumes": 3,
                "max_estimated_spend_usd": "5.00",
                "max_sol_calls": 12,
                "max_codex_jobs": 3,
            },
        }
        with (
            patch.object(recovery, "_rearm_receipt", return_value=policy_receipt),
            patch.object(recovery, "validate_failed_architect", return_value=({}, Decimal("0.5"), ["observed"])),
            patch.object(base, "recent_receipts", return_value=[]),
            patch.object(recovery, "_active_architect_or_codex", return_value=True),
            patch.object(base, "dispatch_architect") as dispatch,
        ):
            receipt = recovery.recover("token", 42, 7)
        self.assertEqual(receipt["stop_reason"], "ACTIVE_WORK_PRESENT")
        self.assertFalse(receipt["dispatched"])
        dispatch.assert_not_called()

    def test_recovery_dispatches_once_with_budget_reserve_intact(self):
        with (
            patch.object(recovery, "_rearm_receipt", return_value={}),
            patch.object(recovery, "validate_failed_architect", return_value=({}, Decimal("0.500000"), ["observed"])),
            patch.object(base, "recent_receipts", return_value=[]),
            patch.object(recovery, "_active_architect_or_codex", return_value=False),
            patch.object(base, "dispatch_architect", return_value=12345) as dispatch,
        ):
            receipt = recovery.recover("token", 42, 7)
        self.assertTrue(receipt["dispatched"])
        self.assertEqual(receipt["child_architect_run_id"], 12345)
        self.assertEqual(receipt["sol_calls_used"], 1)
        self.assertEqual(receipt["estimated_spend_usd"], "0.500000")
        dispatch.assert_called_once_with("token")

    def test_non_allowlisted_incomplete_reason_fails_closed(self):
        run = {
            "id": 50,
            "name": recovery.ARCHITECT_WORKFLOW_NAME,
            "path": recovery.ARCHITECT_WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "head_branch": "main",
            "run_attempt": 1,
            "head_sha": "a" * 40,
        }
        steps = {name: "success" for name in recovery.REQUIRED_SUCCESS_STEPS}
        steps[recovery.SOL_STEP] = "failure"
        steps.update({name: "skipped" for name in recovery.REQUIRED_SKIPPED_STEPS})
        usage = {
            "response_status": "incomplete",
            "incomplete_reason": "content_filter",
            "model": "gpt-5.6-sol",
            "input_tokens": 1,
            "output_tokens": 1,
        }
        with (
            patch.object(base, "api_json", return_value=run),
            patch.object(recovery, "_job_steps", return_value=steps),
            patch.object(base, "artifact_bytes", return_value=b"zip"),
            patch.object(recovery, "_artifact_names", return_value={"sol-usage-initial.json"}),
            patch.object(base, "extract_json", return_value=usage),
        ):
            with self.assertRaises(base.ResumeError):
                recovery.validate_failed_architect("token", 50)


if __name__ == "__main__":
    unittest.main()
