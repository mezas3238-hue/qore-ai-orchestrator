from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rearm_autonomy as rearm


class RearmAutonomyTests(unittest.TestCase):
    def stopped(self, reason: str = "ESTIMATED_SPEND_CAP_REACHED") -> dict[str, object]:
        return {
            "schema_version": "qore.orchestration.resume.receipt.v1",
            "event_key": "CODEX:repo:100:1",
            "actor": "CODEX",
            "repository": "mezas3238-hue/qore-ai-orchestrator",
            "source_run_id": 100,
            "source_run_attempt": 1,
            "source_conclusion": "success",
            "package_id": "QORE-CODEX-aaaaaaaaaaaa-0123456789abcdef",
            "parent_architect_run_id": 77,
            "session_id": "QORE-ORCH-R77",
            "cycle_index": 3,
            "max_auto_resumes": 3,
            "estimated_spend_usd": "5.100000",
            "max_estimated_spend_usd": "5.00",
            "architect_cost_usd": "0.100000",
            "architect_cost_notes": [],
            "agent_cost_usd": "0.200000",
            "agent_cost_kind": "observed",
            "sol_calls_used": 8,
            "max_sol_calls": 12,
            "sol_calls_reserved_per_architect_run": 3,
            "codex_jobs_used": 3,
            "max_codex_jobs": 3,
            "package_history": ["QORE-CODEX-aaaaaaaaaaaa-0123456789abcdef"],
            "dispatched": False,
            "child_architect_run_id": None,
            "stop_reason": reason,
            "production_authority": False,
        }

    def rearm_request(self, run_id: int = 123) -> dict[str, object]:
        return {
            "schema_version": rearm.REARM_REQUEST_SCHEMA,
            "stopped_resume_run_id": run_id,
            "confirmation": rearm.REARM_CONFIRMATION,
            "reason": "User explicitly authorized one new bounded validation tranche.",
            "production_authority": False,
        }

    def test_all_budget_stop_reasons_are_rearmable(self):
        for reason in sorted(rearm.ALLOWED_STOP_REASONS):
            with self.subTest(reason=reason):
                receipt = rearm.validate_stopped_receipt(self.stopped(reason))
                self.assertEqual(receipt["stop_reason"], reason)

    def test_non_budget_stop_cannot_be_rearmed(self):
        with self.assertRaisesRegex(rearm.RearmError, "not eligible"):
            rearm.validate_stopped_receipt(self.stopped("LOOP_SIGNATURE_REPEATED_PACKAGE"))

    def test_dispatched_receipt_cannot_be_rearmed(self):
        stopped = self.stopped()
        stopped["dispatched"] = True
        stopped["child_architect_run_id"] = 999
        with self.assertRaisesRegex(rearm.RearmError, "undispatched"):
            rearm.validate_stopped_receipt(stopped)

    def test_missing_usage_counters_fail_closed(self):
        stopped = self.stopped()
        stopped.pop("sol_calls_used")
        with self.assertRaisesRegex(rearm.RearmError, "sol_calls_used"):
            rearm.validate_stopped_receipt(stopped)

    def test_bad_production_boundary_fails_closed(self):
        stopped = self.stopped()
        stopped["production_authority"] = True
        with self.assertRaisesRegex(rearm.RearmError, "production boundary"):
            rearm.validate_stopped_receipt(stopped)

    def test_rearm_request_is_exact_and_has_no_production_authority(self):
        value = self.rearm_request()
        self.assertEqual(rearm.validate_rearm_request(value)["stopped_resume_run_id"], 123)
        extra = dict(value, extra="forbidden")
        with self.assertRaisesRegex(rearm.RearmError, "keys are not exact"):
            rearm.validate_rearm_request(extra)
        production = dict(value, production_authority=True)
        with self.assertRaisesRegex(rearm.RearmError, "production boundary"):
            rearm.validate_rearm_request(production)
        bad_confirmation = dict(value, confirmation="YES")
        with self.assertRaisesRegex(rearm.RearmError, "confirmation"):
            rearm.validate_rearm_request(bad_confirmation)

    def test_push_activation_requires_exact_one_file_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            request_path = Path(temp) / "request.json"
            request_path.write_text(json.dumps(self.rearm_request()) + "\n", encoding="utf-8")
            sha = "a" * 40
            good_commit = {"sha": sha, "files": [{"filename": rearm.REARM_REQUEST_PATH}]}
            with mock.patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": sha},
                clear=False,
            ), mock.patch.object(rearm.resume, "api_json", return_value=good_commit):
                value = rearm.validate_push_activation("token", request_path)
                self.assertEqual(value["stopped_resume_run_id"], 123)

            bad_commit = {
                "sha": sha,
                "files": [
                    {"filename": rearm.REARM_REQUEST_PATH},
                    {"filename": "scripts/rearm_autonomy.py"},
                ],
            }
            with mock.patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": sha},
                clear=False,
            ), mock.patch.object(rearm.resume, "api_json", return_value=bad_commit):
                with self.assertRaisesRegex(rearm.RearmError, "exactly the rearm request file"):
                    rearm.validate_push_activation("token", request_path)

    def test_push_activation_rejects_non_main_or_non_push(self):
        with tempfile.TemporaryDirectory() as temp:
            request_path = Path(temp) / "request.json"
            request_path.write_text(json.dumps(self.rearm_request()) + "\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": "a" * 40},
                clear=False,
            ):
                with self.assertRaisesRegex(rearm.RearmError, "trusted push"):
                    rearm.validate_push_activation("token", request_path)

    def test_rearm_receipt_preserves_prior_audit_evidence(self):
        stopped = self.stopped()
        receipt = rearm.build_rearm_receipt(
            stopped,
            stopped_resume_run_id=123,
            rearm_run_id=456,
            child_architect_run_id=789,
        )
        self.assertEqual(receipt["rearmed_from_resume_run_id"], 123)
        self.assertEqual(receipt["prior_session_id"], "QORE-ORCH-R77")
        self.assertEqual(receipt["prior_estimated_spend_usd"], "5.100000")
        self.assertEqual(receipt["prior_sol_calls_used"], 8)
        self.assertEqual(receipt["prior_codex_jobs_used"], 3)
        self.assertEqual(receipt["new_session_seed_architect_run_id"], 789)
        self.assertEqual(receipt["new_session_policy"]["max_auto_resumes"], 3)
        self.assertEqual(receipt["new_session_policy"]["max_estimated_spend_usd"], "5.00")
        self.assertFalse(receipt["production_authority"])

    def test_rearm_run_ids_must_be_positive(self):
        with self.assertRaisesRegex(rearm.RearmError, "positive integers"):
            rearm.build_rearm_receipt(
                self.stopped(),
                stopped_resume_run_id=0,
                rearm_run_id=1,
                child_architect_run_id=2,
            )

    def test_source_run_must_be_exact_completion_gate(self):
        run = {
            "id": 99,
            "name": rearm.RESUME_WORKFLOW_NAME,
            "event": "repository_dispatch",
            "status": "completed",
            "head_branch": "main",
            "head_sha": "a" * 40,
        }
        rearm.validate_resume_run(run, 99)
        run["name"] = "Other workflow"
        with self.assertRaisesRegex(rearm.RearmError, "completion resume gate"):
            rearm.validate_resume_run(run, 99)


if __name__ == "__main__":
    unittest.main()
