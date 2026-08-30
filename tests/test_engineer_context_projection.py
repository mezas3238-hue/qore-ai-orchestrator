from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import augment_engineer_context as subject  # noqa: E402


class EngineerReviewerProjectionTests(unittest.TestCase):
    def _external(self) -> dict[str, object]:
        huge = "X" * 30000
        run = {
            "id": 123,
            "name": "Reviewer exact-QG free probes",
            "display_title": "probe",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "a" * 40,
            "run_attempt": 1,
            "created_at": "2026-08-30T00:00:00Z",
            "updated_at": "2026-08-30T00:01:00Z",
        }
        control = {
            "repository": "mezas3238-hue/qore-deepseek-reviewer",
            "visibility": "AVAILABLE",
            "main": {
                "sha": "b" * 40,
                "tree_sha": "c" * 40,
                "parents": ["d" * 40],
                "signature_verified": True,
                "signature_reason": "valid",
                "commit_message": "live reviewer main",
            },
            "open_pull_requests": [
                {
                    "number": 9,
                    "title": "bounded work",
                    "state": "open",
                    "draft": False,
                    "base_sha": "e" * 40,
                    "head_sha": "f" * 40,
                    "body": huge,
                    "latest_head_run": run,
                }
            ],
            "open_issues": [
                {
                    "number": 17,
                    "title": "reviewer hardening",
                    "state": "open",
                    "labels": ["infra"],
                    "body": huge,
                }
            ],
            "recent_action_runs": [copy.deepcopy(run) for _ in range(12)],
            "recent_closed_issues": [{"number": 21, "body": huge}],
            "recent_closed_pull_requests": [{"number": 25, "body": huge}],
            "technical_projection": {
                "bound_main_sha": "b" * 40,
                "authoritative_model": "deepseek-v4-pro",
                "governance_alignment": True,
                "operational_default": {
                    "profile": "stable",
                    "entrypoint": "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
                    "manifest_governed": True,
                },
                "stable_contract": {
                    "completion_budget_default": 100000,
                    "verdict_reserve_default": 12000,
                    "flash_substitution": False,
                },
                "alternate_profiles": {
                    "compact-budgeted": {
                        "entrypoint": "scripts/deepseek_reviewer_compact_budgeted_v20.py",
                        "ordinary_default": False,
                        "promoted_to_stable": False,
                    }
                },
                "governance_resolution": {
                    "stable_profile_recertified_and_live": True,
                    "compact_v20_equivalence_or_stable_promotion": "ABSENT_AND_NOT_REQUIRED_FOR_ORDINARY_ROUTE",
                },
                "binding_contract": {"run_name_package_bound": True},
                "qg_transport_contract": {
                    "transported_qg_evidence_max_chars": 8000,
                    "stable_profile_bound": True,
                },
                "stable_manifest": {"blob_sha": "9" * 40, "exact_blob_binding_verified": True},
                "files": [{"path": "noise", "blob_sha": "1" * 40, "content": huge}],
                "stable_profile_authorized_workflows": {"files": [huge]},
            },
        }
        return {
            "schema_version": "qore.external.reviewers.v1",
            "configured": True,
            "errors": [],
            "claude": {
                "repository": "mezas3238-hue/qore-claude-reviewer",
                "status": "COMPLETED",
                "current_request": {"package_id": "QORE-SOL-ABC-CLAUDE-R1", "pr_number": 466},
                "artifact": {"id": 1, "name": "claude-package", "digest": "sha256:test"},
                "review": {"verdict": "CLEAN", "text": huge},
                "control_plane": {
                    **control,
                    "repository": "mezas3238-hue/qore-claude-reviewer",
                    "technical_projection": None,
                },
            },
            "deepseek": {
                "repository": "mezas3238-hue/qore-deepseek-reviewer",
                "status": "REQUEST_PRESENT",
                "result_source": "qore-core pull-request reviews",
                "current_request": {"package_id": "QORE-SOL-ABC-DS-EXPERT-R1", "pr_number": 466},
                "control_plane": control,
            },
        }

    def test_projection_preserves_operational_identity_and_omits_large_prose(self) -> None:
        projected = subject.compact_external_for_engineer(self._external())
        encoded = str(projected)
        deepseek = projected["deepseek"]
        control = deepseek["control_plane"]
        technical = control["technical_projection"]

        self.assertEqual(deepseek["current_request"]["pr_number"], 466)
        self.assertEqual(control["main"]["sha"], "b" * 40)
        self.assertEqual(technical["authoritative_model"], "deepseek-v4-pro")
        self.assertTrue(technical["governance_alignment"])
        self.assertEqual(technical["operational_default"]["profile"], "stable")
        self.assertFalse(technical["alternate_profiles"]["compact-budgeted"]["ordinary_default"])
        self.assertTrue(technical["qg_transport_contract"]["stable_profile_bound"])
        self.assertEqual(len(control["recent_action_runs"]), subject.MAX_ENGINEER_REVIEWER_RUNS)
        self.assertNotIn("recent_closed_issues", control)
        self.assertNotIn("recent_closed_pull_requests", control)
        self.assertNotIn("files", technical)
        self.assertNotIn("stable_manifest", technical)
        self.assertNotIn("text", projected["claude"]["review"])
        self.assertNotIn("X" * 1000, encoded)

    def test_large_architect_reviewer_state_stays_bounded_for_engineer(self) -> None:
        context = {
            "dynamic_context": {"external_reviewer_state": self._external()},
            "engineer_context": {"base": "B" * 50000},
            "metrics": {},
        }
        augmented = subject.augment(context)
        chars = subject.compact_chars(augmented["engineer_context"])
        self.assertLess(chars, subject.MAX_ENGINEER_CONTEXT_CHARS)
        self.assertEqual(augmented["metrics"]["engineer_context_chars"], chars)

    def test_hard_engineer_bound_still_fails_closed(self) -> None:
        context = {
            "dynamic_context": {"external_reviewer_state": self._external()},
            "engineer_context": {"base": "B" * subject.MAX_ENGINEER_CONTEXT_CHARS},
            "metrics": {},
        }
        with self.assertRaisesRegex(ValueError, "engineer context exceeds bound"):
            subject.augment(context)


if __name__ == "__main__":
    unittest.main()
