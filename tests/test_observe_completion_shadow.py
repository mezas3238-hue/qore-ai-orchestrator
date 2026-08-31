from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import observe_completion_shadow as target


class CompletionShadowObserverTests(unittest.TestCase):
    @patch.object(target, "_codex_completion")
    def test_codex_repository_dispatch_routes_without_spend(self, codex):
        codex.return_value = {"shadow_only": True, "would_spend_api": False}
        event = {"client_payload": {"actor": "CODEX"}}
        result = target.observe(
            event,
            "repository_dispatch",
            orchestrator_token="token",
            reviewer_token="reviewer",
        )
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["would_spend_api"])
        codex.assert_called_once()

    @patch.object(target, "_reviewer_completion")
    def test_reviewer_repository_dispatch_routes_read_only(self, reviewer):
        reviewer.return_value = {"shadow_only": True, "would_dispatch": False}
        event = {"client_payload": {"actor": "DEEPSEEK"}}
        result = target.observe(
            event,
            "repository_dispatch",
            orchestrator_token="token",
            reviewer_token="reviewer-token",
        )
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["would_dispatch"])

    def test_reviewer_event_without_verification_token_fails_closed(self):
        event = {"client_payload": {"actor": "DEEPSEEK"}}
        with self.assertRaisesRegex(Exception, "reviewer token"):
            target.observe(
                event,
                "repository_dispatch",
                orchestrator_token="token",
                reviewer_token="",
            )

    def test_manual_dispatch_is_diagnostic_only(self):
        result = target.observe(
            {},
            "workflow_dispatch",
            orchestrator_token="",
            reviewer_token="",
        )
        self.assertEqual(result["shadow_action"], "DIAGNOSTIC_ONLY")
        self.assertFalse(result["would_spend_api"])
        self.assertFalse(result["production_authority"])


if __name__ == "__main__":
    unittest.main()
