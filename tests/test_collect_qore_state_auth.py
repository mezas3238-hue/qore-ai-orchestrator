from __future__ import annotations

import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_qore_state as state


class CollectQoreStateAuthTests(unittest.TestCase):
    def test_public_fallback_omits_authorization_header(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            headers = state.github_headers()
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")

    def test_actions_token_is_used_only_as_bearer_header(self) -> None:
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": " bounded-read-token \n"}, clear=True):
            headers = state.github_headers()
        self.assertEqual(headers["Authorization"], "Bearer bounded-read-token")
        self.assertEqual(headers["Accept"], "application/vnd.github+json")

    def test_http_failure_evidence_never_contains_token(self) -> None:
        token = "never-log-this-token"
        error = urllib.error.HTTPError(
            "https://api.github.com/example",
            403,
            "forbidden",
            hdrs=None,
            fp=None,
        )
        errors: list[str] = []
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": token}, clear=True), mock.patch(
            "collect_qore_state.urllib.request.urlopen", side_effect=error
        ):
            self.assertIsNone(state.api_json("/branches/main", None, errors))
        self.assertEqual(errors, ["github_api:/branches/main:HTTPError"])
        self.assertNotIn(token, "".join(errors))

    def test_autonomous_workflow_injects_only_read_token_into_snapshot_step(self) -> None:
        workflow = (ROOT / ".github/workflows/qore-architect-autonomous-v2.yml").read_text(
            encoding="utf-8"
        )
        expected = (
            "      - name: Build canonical QORE state snapshot\n"
            "        env:\n"
            "          GITHUB_TOKEN: ${{ github.token }}\n"
            "        run: |\n"
        )
        self.assertIn(expected, workflow)
        self.assertIn("permissions:\n  contents: read\n  actions: write\n", workflow)


if __name__ == "__main__":
    unittest.main()
