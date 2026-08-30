from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import augment_engineer_context
import augment_reviewer_control_plane
import prepare_sol_continuation_context
import validate_architect_continuation


class AutonomousContinuationTests(unittest.TestCase):
    MAIN = "a" * 40

    def disabled_engineering(self):
        return {
            "enabled": False,
            "contract_id": "",
            "target_repository": "",
            "objective": "",
            "scope": [],
            "acceptance": [],
            "required_tests": [],
            "forbidden": [],
        }

    def disabled_review(self):
        return {
            "enabled": False,
            "contract_id": "",
            "pr_number": 0,
            "review_kind": "NONE",
            "objective": "",
            "scope": [],
            "adversarial_foci": [],
            "acceptance": [],
            "forbidden": [],
        }

    def disabled_wait(self):
        return {"enabled": False, "actor": "NONE", "package_id": "", "reason": ""}

    def snapshot(self):
        return {
            "main_sha": self.MAIN,
            "external_reviewer_state": {
                "deepseek": {
                    "current_request": {"package_id": "PKG-DS"},
                    "control_plane": {
                        "visibility": "AVAILABLE",
                        "recent_action_runs": [
                            {"id": 1, "status": "in_progress", "head_sha": "b" * 40}
                        ],
                    },
                },
                "claude": {
                    "current_request": {"package_id": "PKG-CL"},
                    "control_plane": {
                        "visibility": "AVAILABLE",
                        "recent_action_runs": [],
                    },
                },
            },
        }

    def base_decision(self):
        return {
            "source_main_sha": self.MAIN,
            "production_authority": False,
            "engineering_contract": self.disabled_engineering(),
            "review_contract": self.disabled_review(),
            "wait_state": self.disabled_wait(),
            "evidence_requests": [],
            "status": "PROGRAM_COMPLETE",
            "next_actor": "NONE",
        }

    def test_no_action_is_not_a_valid_autonomous_status(self):
        decision = self.base_decision()
        decision["status"] = "NO_ACTION"
        with self.assertRaisesRegex(ValueError, "not an autonomous-continuation status"):
            validate_architect_continuation.validate(decision, self.snapshot())

    def test_engineering_task_can_target_deepseek_reviewer(self):
        decision = self.base_decision()
        decision["status"] = "ENGINEERING_TASK"
        decision["next_actor"] = "CODEX"
        decision["engineering_contract"] = {
            "enabled": True,
            "contract_id": "ENG-1",
            "target_repository": "mezas3238-hue/qore-deepseek-reviewer",
            "objective": "repair reviewer context budget",
            "scope": ["reviewer"],
            "acceptance": ["green probes"],
            "required_tests": ["probe"],
            "forbidden": ["no Core mutation"],
        }
        validate_architect_continuation.validate(decision, self.snapshot())

    def test_waiting_agent_requires_observed_pending_run(self):
        decision = self.base_decision()
        decision["status"] = "WAITING_AGENT"
        decision["wait_state"] = {
            "enabled": True,
            "actor": "DEEPSEEK",
            "package_id": "PKG-DS",
            "reason": "exact reviewer job is running",
        }
        validate_architect_continuation.validate(decision, self.snapshot())

        decision["wait_state"]["actor"] = "CLAUDE_CODE"
        decision["wait_state"]["package_id"] = "PKG-CL"
        with self.assertRaisesRegex(ValueError, "queued/in-progress"):
            validate_architect_continuation.validate(decision, self.snapshot())

    def test_failed_or_completed_request_is_not_a_wait_boundary(self):
        snapshot = self.snapshot()
        snapshot["external_reviewer_state"]["deepseek"]["control_plane"]["recent_action_runs"] = [
            {"id": 2, "status": "completed", "conclusion": "failure"}
        ]
        decision = self.base_decision()
        decision["status"] = "WAITING_AGENT"
        decision["wait_state"] = {
            "enabled": True,
            "actor": "DEEPSEEK",
            "package_id": "PKG-DS",
            "reason": "request exists",
        }
        with self.assertRaisesRegex(ValueError, "queued/in-progress"):
            validate_architect_continuation.validate(decision, snapshot)

    def test_reconstruction_is_nonterminal_and_requires_evidence_requests(self):
        decision = self.base_decision()
        decision["status"] = "RECONSTRUCTION_REQUIRED"
        decision["next_actor"] = "SOL"
        with self.assertRaisesRegex(ValueError, "evidence_requests"):
            validate_architect_continuation.validate(decision, self.snapshot())
        decision["evidence_requests"] = ["refresh reviewer control plane"]
        validate_architect_continuation.validate(decision, self.snapshot())

    def test_control_plane_matches_latest_run_by_exact_head(self):
        head = "c" * 40
        runs = [
            {"id": 1, "head_sha": head, "status": "completed", "conclusion": "success"},
            {"id": 2, "head_sha": "d" * 40, "status": "in_progress", "conclusion": None},
        ]
        observed = augment_reviewer_control_plane._latest_run_for_head(runs, head)
        self.assertEqual(observed["id"], 1)
        self.assertEqual(observed["conclusion"], "success")

    def test_engineer_context_receives_reviewer_control_plane(self):
        context = {
            "dynamic_context": {"external_reviewer_state": {"deepseek": {"control_plane": {"visibility": "AVAILABLE"}}}},
            "engineer_context": {"repository": "mezas3238-hue/qore-core"},
            "metrics": {},
        }
        result = augment_engineer_context.augment(context)
        self.assertIn("external_reviewer_state", result["engineer_context"])
        self.assertGreater(result["metrics"]["engineer_context_chars"], 0)

    def test_reconstruction_continuation_carries_prior_decision(self):
        context = {
            "stable_context": {"roadmap": "x"},
            "dynamic_context": {"source_main_sha": self.MAIN},
            "metrics": {},
        }
        decision = self.base_decision()
        decision["status"] = "RECONSTRUCTION_REQUIRED"
        decision["next_actor"] = "SOL"
        decision["evidence_requests"] = ["refresh"]
        result = prepare_sol_continuation_context.prepare(context, decision)
        continuation = result["dynamic_context"]["controller_continuation"]
        self.assertEqual(continuation["kind"], "RECONSTRUCTION_CONTINUATION")
        self.assertEqual(continuation["prior_decision"]["status"], "RECONSTRUCTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
