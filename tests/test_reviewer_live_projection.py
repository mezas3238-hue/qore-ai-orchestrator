from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import augment_reviewer_control_plane as control


class ReviewerLiveProjectionTests(unittest.TestCase):
    @staticmethod
    def _payload(text: str, sha: str = "b" * 40) -> dict[str, object]:
        return {
            "type": "file",
            "sha": sha,
            "size": len(text.encode("utf-8")),
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }

    def _texts(self) -> dict[str, str]:
        return {
            "scripts/run_review_with_meter.py": "\n".join(
                [
                    '"deepseek_reviewer_v2_1_1_entrypoint.py"',
                    '"deepseek_reviewer_compact_budgeted_v20.py"',
                    '_REVIEWER_PROFILE = os.environ.get("DEEPSEEK_REVIEWER_PROFILE", "compact-budgeted")',
                    'elif _REVIEWER_PROFILE == "compact-budgeted":',
                    'elif _REVIEWER_PROFILE == "stable":',
                ]
            ),
            "scripts/deepseek_reviewer_v2_1_1_entrypoint.py": "\n".join(
                [
                    "deepseek_reviewer_v2_0_entrypoint",
                    "deepseek_reviewer_v2_1_entrypoint",
                    "DEEPSEEK_MAX_MANDATORY_CHANGED_CHARS",
                    "DEEPSEEK_MAX_FINAL_EVIDENCE_CHARS",
                ]
            ),
            "scripts/deepseek_reviewer_v2_1_entrypoint.py": "\n".join(
                [
                    '"DEEPSEEK_TOTAL_COMPLETION_TOKEN_BUDGET", "100000"',
                    '"DEEPSEEK_VERDICT_RESERVE_TOKENS", "12000"',
                    "thinking=True",
                    "thinking=False",
                    "v2_1_same_model_extractor=True",
                    "v2_1_flash_substitution=False",
                    "v2_1_cot_continuation=False",
                    'if stage == "final-fallback":',
                ]
            ),
            "scripts/deepseek_reviewer_compact_budgeted_v20.py": (
                "deepseek_reviewer_compact_budgeted_v19\n_scanner_r62g_exact\n"
            ),
            "scripts/deepseek_reviewer_compact_budgeted_v19.py": "\n".join(
                [
                    "_QG_EVIDENCE_MAX_CHARS = 8000",
                    "Full command windows were parsed and validated internally",
                    "authenticated_command_summaries",
                    "compact QORE CI evidence exceeds its hard transport bound",
                ]
            ),
            "scripts/qg_package_contract.py": "contract\n",
            ".github/workflows/deepseek-auto-dispatch.yml": "\n".join(
                [
                    "requests/current.json",
                    "benchmarks/current.json",
                    "Refusing ambiguous push: review and benchmark requests changed together.",
                    "scripts/qg_package_contract.py",
                    "gh workflow run deepseek-qore-review.yml",
                ]
            ),
            ".github/workflows/deepseek-connection-test.yml": "name: connection\n",
            ".github/workflows/deepseek-qore-review.yml": "\n".join(
                [
                    "run-name: DeepSeek QORE review · ${{ inputs.package_id }}",
                    "DEEPSEEK_MODEL: deepseek-v4-pro",
                    'test "$LIVE_BASE" = "$EXPECTED_BASE"',
                    'test "$LIVE_HEAD" = "$EXPECTED_HEAD"',
                    'test "$LIVE_SYNTHETIC" = "$EXPECTED_SYNTHETIC"',
                    'test "$PARENTS" = "$EXPECTED_BASE $EXPECTED_HEAD"',
                    'test "$SYNTHETIC_TREE" = "$HEAD_TREE"',
                    "Revalidate complete frozen PR immediately before publication",
                ]
            ),
        }

    def _fake_contents_api(self, texts: dict[str, str]):
        def fake(repo: str, path: str, token: str, params=None):
            self.assertEqual(repo, control.DEEPSEEK_REPO)
            self.assertEqual(token, "token")
            self.assertEqual(params, {"ref": "a" * 40})
            self.assertTrue(path.startswith("/contents/"))
            file_path = unquote(path.removeprefix("/contents/"))
            return self._payload(texts[file_path])

        return fake

    def test_projection_binds_exact_main_and_distinguishes_live_default(self):
        texts = self._texts()
        with patch.object(control, "_api_json", side_effect=self._fake_contents_api(texts)):
            projection = control._deepseek_projection("token", "a" * 40)

        self.assertEqual(projection["bound_main_sha"], "a" * 40)
        self.assertEqual(projection["authoritative_model"], "deepseek-v4-pro")
        self.assertEqual(projection["operational_default"]["profile"], "compact-budgeted")
        self.assertEqual(
            projection["operational_default"]["entrypoint"],
            "scripts/deepseek_reviewer_compact_budgeted_v20.py",
        )
        self.assertEqual(projection["stable_fallback"]["profile"], "stable")
        self.assertEqual(projection["stable_fallback"]["completion_budget_default"], 100000)
        self.assertEqual(projection["stable_fallback"]["verdict_reserve_default"], 12000)
        self.assertEqual(projection["stable_profile_authorized_workflows"]["count"], 3)
        self.assertTrue(projection["binding_contract"]["run_name_package_bound"])
        self.assertEqual(projection["qg_transport_contract"]["transported_qg_evidence_max_chars"], 8000)
        self.assertEqual(len(projection["files"]), len(control.DEEPSEEK_PROJECTION_FILES))

    def test_projection_fails_closed_if_live_model_marker_changes(self):
        texts = self._texts()
        texts[".github/workflows/deepseek-qore-review.yml"] = texts[
            ".github/workflows/deepseek-qore-review.yml"
        ].replace("DEEPSEEK_MODEL: deepseek-v4-pro", "DEEPSEEK_MODEL: unexpected")
        with patch.object(control, "_api_json", side_effect=self._fake_contents_api(texts)):
            with self.assertRaisesRegex(control.ControlPlaneError, "required live-contract markers missing"):
                control._deepseek_projection("token", "a" * 40)

    def test_main_identity_binds_sha_tree_parent_and_signature(self):
        payload = {
            "commit": {
                "sha": "a" * 40,
                "commit": {
                    "message": "live main",
                    "tree": {"sha": "b" * 40},
                    "verification": {"verified": True, "reason": "valid"},
                },
                "parents": [{"sha": "c" * 40}],
            }
        }
        with patch.object(control, "_api_json", return_value=payload):
            identity = control._main_identity(control.DEEPSEEK_REPO, "token")
        self.assertEqual(identity["sha"], "a" * 40)
        self.assertEqual(identity["tree_sha"], "b" * 40)
        self.assertEqual(identity["parents"], ["c" * 40])
        self.assertTrue(identity["signature_verified"])

    def test_content_decoder_rejects_non_base64(self):
        with self.assertRaisesRegex(control.ControlPlaneError, "valid UTF-8/base64"):
            control._decode_text_content(
                {"type": "file", "content": "%%%not-base64%%%"},
                "bad",
            )


if __name__ == "__main__":
    unittest.main()
