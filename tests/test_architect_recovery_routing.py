from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import select_architect_recovery_mode as routing
import resume_after_agent_completion as base


class ArchitectRecoveryRoutingTests(unittest.TestCase):
    SHA = "a" * 40

    def commit(self, *paths: str, sha: str | None = None):
        return {
            "sha": self.SHA if sha is None else sha,
            "files": [{"filename": path} for path in paths],
        }

    def test_post_spend_routes_from_exact_commit_file(self):
        with patch.object(base, "api_json", return_value=self.commit(routing.POST_SPEND_PATH)):
            self.assertEqual(routing.select_mode("token", self.SHA), "post_spend")

    def test_pre_spend_remains_supported(self):
        with patch.object(base, "api_json", return_value=self.commit(routing.PRE_SPEND_PATH)):
            self.assertEqual(routing.select_mode("token", self.SHA), "pre_spend")

    def test_commit_binding_mismatch_fails_closed(self):
        with patch.object(base, "api_json", return_value=self.commit(routing.POST_SPEND_PATH, sha="b" * 40)):
            with self.assertRaises(routing.RoutingError):
                routing.select_mode("token", self.SHA)

    def test_multiple_paths_fail_closed(self):
        with patch.object(
            base,
            "api_json",
            return_value=self.commit(routing.POST_SPEND_PATH, "docs/also-changed.md"),
        ):
            with self.assertRaises(routing.RoutingError):
                routing.select_mode("token", self.SHA)

    def test_unknown_single_path_fails_closed(self):
        with patch.object(base, "api_json", return_value=self.commit("recovery/unknown.json")):
            with self.assertRaises(routing.RoutingError):
                routing.select_mode("token", self.SHA)

    def test_invalid_sha_fails_closed_without_api_call(self):
        with patch.object(base, "api_json") as api:
            with self.assertRaises(routing.RoutingError):
                routing.select_mode("token", "not-a-sha")
        api.assert_not_called()

    def test_missing_or_invalid_file_evidence_fails_closed(self):
        for payload in (
            {"sha": self.SHA},
            {"sha": self.SHA, "files": "not-a-list"},
            {"sha": self.SHA, "files": [{}]},
            {"sha": self.SHA, "files": [{"filename": ""}]},
        ):
            with self.subTest(payload=payload), patch.object(base, "api_json", return_value=payload):
                with self.assertRaises(routing.RoutingError):
                    routing.select_mode("token", self.SHA)

    def test_api_failure_propagates_fail_closed(self):
        with patch.object(base, "api_json", side_effect=base.ResumeError("api failed")):
            with self.assertRaises(base.ResumeError):
                routing.select_mode("token", self.SHA)


if __name__ == "__main__":
    unittest.main()
