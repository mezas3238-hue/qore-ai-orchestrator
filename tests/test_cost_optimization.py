from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_model_context
import run_sol_architect_v2
import select_sol_reasoning_v2


class CostOptimizationTests(unittest.TestCase):
    def snapshot(self) -> dict:
        main = "a" * 40
        return {
            "schema_version": "qore.state.snapshot.v1",
            "repository": "mezas3238-hue/qore-core",
            "collected_at_utc": "2026-08-30T05:00:00+00:00",
            "main_sha": main,
            "live_main_sha": main,
            "tree_sha": "b" * 40,
            "snapshot_consistent": True,
            "branch_protection": {"protected": True, "required_status_contexts": ["quality"]},
            "collection_errors": [],
            "recent_commits": [{"sha": "c" * 40, "subject": "current"}],
            "open_pull_requests": [
                {
                    "number": 10,
                    "title": "Recursive semantic identity correction",
                    "state": "open",
                    "draft": True,
                    "updated_at": "2026-08-30T04:00:00Z",
                    "base_sha": main,
                    "head_sha": "d" * 40,
                    "synthetic_sha": "e" * 40,
                    "body": "Closes #20",
                    "reviews": [],
                    "conversation_comments": [{"id": 1, "user": "u", "body": "Reviewer issue #777 remains pending."}],
                },
                {
                    "number": 11,
                    "title": "Older work",
                    "state": "open",
                    "draft": True,
                    "updated_at": "2026-01-01T00:00:00Z",
                    "base_sha": main,
                    "head_sha": "f" * 40,
                    "synthetic_sha": "1" * 40,
                    "body": "UNFOCUSED_PR_BODY",
                    "reviews": [],
                    "conversation_comments": [],
                },
            ],
            "open_issues": [
                {"number": 20, "title": "Registry revalidation", "state": "open", "updated_at": "2026-08-30T04:00:00Z", "labels": [], "body": "FOCUSED_ISSUE_BODY with credential-like material."},
                {"number": 99, "title": "Future security boundary work", "state": "open", "updated_at": "2026-01-01T00:00:00Z", "labels": ["future"], "body": "UNRELATED_BACKLOG_BODY_" + ("X" * 12000)},
            ],
            "recent_main_action_runs": [],
            "readme": "README",
            "constitution_documents": [{"path": "docs/constitution/a.md", "content": "CONSTITUTION"}],
            "roadmap_documents": [{"path": "docs/roadmap/a.md", "content": "ROADMAP"}],
            "mission_document_heads": [{"path": "docs/missions/a.md", "head": "MISSION " + ("M" * 2000)}],
            "architecture_document_paths": ["docs/architecture/a.md"],
            "external_reviewer_state": {
                "configured": True,
                "errors": [],
                "deepseek": {"current_request": {"pr_number": 10, "package_id": "PKG", "expected_head": "d" * 40}, "status": "REQUEST_PRESENT"},
                "claude": {"current_request": {"pr_number": 999, "package_id": "OLD"}, "review": {"verdict": "CLEAN", "text": "R" * 5000}, "status": "COMPLETED"},
            },
        }

    def test_model_context_keeps_focused_evidence_and_omits_backlog_bodies(self):
        snapshot = self.snapshot()
        raw = (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode()
        context = build_model_context.build_context(snapshot, raw)
        rendered = json.dumps(context, ensure_ascii=False)
        self.assertIn("FOCUSED_ISSUE_BODY", rendered)
        self.assertNotIn("UNRELATED_BACKLOG_BODY", rendered)
        self.assertNotIn("UNFOCUSED_PR_BODY", rendered)
        self.assertEqual(context["metrics"]["focused_pr_numbers"], [10])
        self.assertEqual(context["metrics"]["focused_issue_numbers"], [20])
        self.assertLess(context["metrics"]["architect_context_chars"], context["metrics"]["full_snapshot_chars"])
        self.assertLessEqual(context["metrics"]["architect_context_chars"], build_model_context.MAX_ARCHITECT_CONTEXT_CHARS)
        self.assertLessEqual(context["metrics"]["engineer_context_chars"], build_model_context.MAX_ENGINEER_CONTEXT_CHARS)

    def test_stale_claude_review_is_bounded(self):
        snapshot = self.snapshot()
        raw = (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode()
        context = build_model_context.build_context(snapshot, raw)
        review = context["dynamic_context"]["external_reviewer_state"]["claude"]["review"]
        self.assertEqual(len(review["text"]), build_model_context.STALE_CLAUDE_REVIEW_CHARS)
        self.assertTrue(review["text_truncated_for_model_context"])

    def test_unrelated_future_security_backlog_does_not_force_max(self):
        policy = select_sol_reasoning_v2.choose_effort(self.snapshot(), "auto")
        self.assertEqual(policy["selected_effort"], "xhigh")
        self.assertNotEqual(policy["selected_effort"], "max")

    def test_focused_security_boundary_can_use_max(self):
        snapshot = self.snapshot()
        snapshot["open_pull_requests"][0]["title"] = "Security boundary credential exposure"
        policy = select_sol_reasoning_v2.choose_effort(snapshot, "auto")
        self.assertEqual(policy["selected_effort"], "max")

    def test_sol_input_places_stable_corpus_before_live_state(self):
        snapshot = self.snapshot()
        raw = (json.dumps(snapshot, indent=2, sort_keys=True) + "\n").encode()
        context = build_model_context.build_context(snapshot, raw)
        model_input = run_sol_architect_v2._model_input(context, "xhigh")
        content = model_input[0]["content"]
        self.assertIn("STABLE QORE ARCHITECTURAL CORPUS", content[0]["text"])
        self.assertIn("LIVE BOUNDED QORE STATE", content[1]["text"])
        self.assertIn("xhigh", content[1]["text"])


if __name__ == "__main__":
    unittest.main()
