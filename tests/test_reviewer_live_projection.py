from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import augment_reviewer_control_plane as control


class ReviewerLiveProjectionTests(unittest.TestCase):
    @staticmethod
    def _blob(path: str) -> str:
        return hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()

    @staticmethod
    def _payload(text: str, sha: str) -> dict[str, object]:
        return {
            "type": "file",
            "sha": sha,
            "size": len(text.encode("utf-8")),
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        }

    def _texts_and_manifest(self) -> tuple[dict[str, str], dict[str, object]]:
        texts = {
            "scripts/run_review_with_meter.py": "\n".join(
                [
                    '"deepseek_reviewer_v2_1_1_entrypoint.py"',
                    '"deepseek_reviewer_compact_budgeted_v20.py"',
                    '_REVIEWER_PROFILE = os.environ.get("DEEPSEEK_REVIEWER_PROFILE", "stable")',
                    'elif _REVIEWER_PROFILE == "compact-budgeted":',
                    'elif _REVIEWER_PROFILE == "stable":',
                    'if _PACKAGE_ID.startswith("BENCHMARK-COMPACT-"):',
                ]
            ),
            "scripts/deepseek_reviewer_v2_1_1_entrypoint.py": "\n".join(
                [
                    "deepseek_reviewer_v2_0_entrypoint",
                    "deepseek_reviewer_v2_1_entrypoint",
                    "DEEPSEEK_MAX_MANDATORY_CHANGED_CHARS",
                    "DEEPSEEK_MAX_FINAL_EVIDENCE_CHARS",
                    "import exact_qg_evidence as exact_qg",
                    "v21.v13.build_baseline_evidence = _build_baseline_with_exact_qg",
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
            "scripts/exact_qg_evidence.py": "\n".join(
                [
                    "_QG_EVIDENCE_MAX_CHARS = 8000",
                    "EXPECTED_QG_SUMMARY_JSON",
                    "authenticated_command_summaries",
                    "_validate_checkout_synthetic",
                    "Full command windows were parsed and validated internally",
                ]
            ),
            "scripts/qg_package_contract.py": "contract\n",
            "scripts/deepseek_reviewer_compact_budgeted_v20.py": "compact alternate\n",
            "scripts/deepseek_reviewer_v2_1_2_candidate_entrypoint.py": "benchmark candidate\n",
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
        engine_paths = {
            path: self._blob(path)
            for path in (
                "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
                "scripts/deepseek_reviewer_v2_1_entrypoint.py",
                "scripts/exact_qg_evidence.py",
                "scripts/qg_package_contract.py",
            )
        }
        workflow_paths = {
            path: self._blob(path)
            for path in control.DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS
        }
        manifest: dict[str, object] = {
            "profile_id": "QORE-DEEPSEEK-V2.1.1-STABLE",
            "status": "stable",
            "model": "deepseek-v4-pro",
            "entrypoint": "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
            "entrypoint_blob": self._blob("scripts/deepseek_reviewer_v2_1_1_entrypoint.py"),
            "meter": {
                "path": "scripts/run_review_with_meter.py",
                "blob": self._blob("scripts/run_review_with_meter.py"),
                "ordinary_route": "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
                "default_profile": "stable",
            },
            "exact_qg_contract": {
                "required": True,
                "helper": "scripts/exact_qg_evidence.py",
                "helper_blob": self._blob("scripts/exact_qg_evidence.py"),
                "package_contract": "scripts/qg_package_contract.py",
                "package_contract_blob": self._blob("scripts/qg_package_contract.py"),
                "max_chars": 8000,
            },
            "alternate_profiles": {
                "compact-budgeted": {
                    "entrypoint": "scripts/deepseek_reviewer_compact_budgeted_v20.py",
                    "blob": self._blob("scripts/deepseek_reviewer_compact_budgeted_v20.py"),
                    "ordinary_default": False,
                    "activation": "explicit profile only",
                },
                "benchmark-compact": {
                    "entrypoint": "scripts/deepseek_reviewer_v2_1_2_candidate_entrypoint.py",
                    "blob": self._blob("scripts/deepseek_reviewer_v2_1_2_candidate_entrypoint.py"),
                    "ordinary_default": False,
                    "activation": "benchmark package prefix only",
                },
            },
            "engine_files": engine_paths,
            "workflows": workflow_paths,
        }
        return texts, manifest

    def _fake_api(
        self,
        texts: dict[str, str],
        manifest: dict[str, object],
        *,
        second_stable: bool = False,
        drift_path: str | None = None,
    ):
        manifest_path = "profiles/QORE-DEEPSEEK-V2.1.1-STABLE.json"
        manifest_text = json.dumps(manifest, sort_keys=True)
        manifest_blob = self._blob(manifest_path)

        def fake(repo: str, path: str, token: str, params=None):
            self.assertEqual(repo, control.DEEPSEEK_REPO)
            self.assertEqual(token, "token")
            self.assertEqual(params, {"ref": "a" * 40})
            if path == "/contents/profiles":
                result = [
                    {
                        "type": "file",
                        "name": "QORE-DEEPSEEK-V2.1.1-STABLE.json",
                        "path": manifest_path,
                        "sha": manifest_blob,
                    }
                ]
                if second_stable:
                    result.append(
                        {
                            "type": "file",
                            "name": "QORE-DEEPSEEK-V9-STABLE.json",
                            "path": "profiles/QORE-DEEPSEEK-V9-STABLE.json",
                            "sha": "f" * 40,
                        }
                    )
                return result
            self.assertTrue(path.startswith("/contents/"))
            file_path = unquote(path.removeprefix("/contents/"))
            if file_path == manifest_path:
                return self._payload(manifest_text, manifest_blob)
            text = texts[file_path]
            sha = self._blob(file_path)
            if file_path == drift_path:
                sha = "f" * 40
            return self._payload(text, sha)

        return fake

    def test_projection_binds_exact_main_and_governed_stable_default(self):
        texts, manifest = self._texts_and_manifest()
        with patch.object(control, "_api_json", side_effect=self._fake_api(texts, manifest)):
            projection = control._deepseek_projection("token", "a" * 40)

        self.assertEqual(projection["bound_main_sha"], "a" * 40)
        self.assertEqual(projection["authoritative_model"], "deepseek-v4-pro")
        self.assertTrue(projection["governance_alignment"])
        self.assertEqual(projection["stable_manifest"]["stable_manifest_count"], 1)
        self.assertTrue(projection["stable_manifest"]["exact_blob_binding_verified"])
        self.assertEqual(projection["operational_default"]["profile"], "stable")
        self.assertEqual(
            projection["operational_default"]["entrypoint"],
            "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
        )
        self.assertTrue(projection["operational_default"]["manifest_governed"])
        self.assertFalse(projection["alternate_profiles"]["compact-budgeted"]["ordinary_default"])
        self.assertFalse(projection["alternate_profiles"]["compact-budgeted"]["promoted_to_stable"])
        self.assertEqual(projection["stable_contract"]["completion_budget_default"], 100000)
        self.assertEqual(projection["stable_contract"]["verdict_reserve_default"], 12000)
        self.assertEqual(projection["stable_profile_authorized_workflows"]["count"], 3)
        self.assertTrue(projection["binding_contract"]["run_name_package_bound"])
        self.assertEqual(projection["qg_transport_contract"]["transported_qg_evidence_max_chars"], 8000)
        self.assertTrue(projection["qg_transport_contract"]["stable_profile_bound"])

    def test_projection_fails_closed_if_live_model_marker_changes(self):
        texts, manifest = self._texts_and_manifest()
        texts[".github/workflows/deepseek-qore-review.yml"] = texts[
            ".github/workflows/deepseek-qore-review.yml"
        ].replace("DEEPSEEK_MODEL: deepseek-v4-pro", "DEEPSEEK_MODEL: unexpected")
        with patch.object(control, "_api_json", side_effect=self._fake_api(texts, manifest)):
            with self.assertRaisesRegex(control.ControlPlaneError, "required live-contract markers missing"):
                control._deepseek_projection("token", "a" * 40)

    def test_projection_fails_closed_on_manifest_blob_drift(self):
        texts, manifest = self._texts_and_manifest()
        path = "scripts/deepseek_reviewer_v2_1_entrypoint.py"
        with patch.object(
            control,
            "_api_json",
            side_effect=self._fake_api(texts, manifest, drift_path=path),
        ):
            with self.assertRaisesRegex(control.ControlPlaneError, "manifest blob drift"):
                control._deepseek_projection("token", "a" * 40)

    def test_projection_requires_exactly_one_stable_manifest(self):
        texts, manifest = self._texts_and_manifest()
        with patch.object(
            control,
            "_api_json",
            side_effect=self._fake_api(texts, manifest, second_stable=True),
        ):
            with self.assertRaisesRegex(control.ControlPlaneError, "exactly one STABLE profile manifest"):
                control._deepseek_projection("token", "a" * 40)

    def test_projection_rejects_compact_as_ordinary_default(self):
        texts, manifest = self._texts_and_manifest()
        meter = manifest["meter"]
        assert isinstance(meter, dict)
        meter["default_profile"] = "compact-budgeted"
        with patch.object(control, "_api_json", side_effect=self._fake_api(texts, manifest)):
            with self.assertRaisesRegex(control.ControlPlaneError, "does not govern the ordinary meter route"):
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
