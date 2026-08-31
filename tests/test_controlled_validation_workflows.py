from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ControlledValidationWorkflowTests(unittest.TestCase):
    def text(self, name: str) -> str:
        return (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")

    def test_codex_v6_validation_is_manual_explicit_and_nonpublishing(self):
        text = self.text("codex-v6-controlled-validation.yml")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)
        self.assertIn("confirm_api_spend", text)
        self.assertIn("QORE_CODEX_V6_MODE: CONTROLLED_VALIDATION", text)
        self.assertIn("persist-credentials: false", text)
        self.assertNotIn("publish_codex_candidate", text)
        self.assertNotIn("repository_dispatch", text)
        self.assertNotIn("contents: write", text)

    def test_sol_v3_validation_is_manual_single_call_and_non_dispatching(self):
        text = self.text("sol-v3-controlled-validation.yml")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)
        self.assertIn("confirm_api_spend", text)
        self.assertIn("QORE_SOL_V3_MODE: CONTROLLED_VALIDATION", text)
        self.assertIn(".model_calls == 1", text)
        self.assertNotIn("repository_dispatch", text)
        self.assertNotIn("contents: write", text)


if __name__ == "__main__":
    unittest.main()
