from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_codex_engineer_worker_v6 as target


class CodexWorkerV6Tests(unittest.TestCase):
    def repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        subprocess.check_call(["git", "init", "-q"], cwd=root)
        subprocess.check_call(["git", "config", "user.email", "qore@example.invalid"], cwd=root)
        subprocess.check_call(["git", "config", "user.name", "QORE Test"], cwd=root)
        path = root / "src/qore"
        path.mkdir(parents=True)
        (path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_a.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "."], cwd=root)
        subprocess.check_call(["git", "commit", "-q", "-m", "base"], cwd=root)
        source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        return temp, root, source

    def request(self, source: str) -> dict:
        return {
            "schema_version": "qore.codex.engineering.request.v1",
            "package_id": f"QORE-CODEX-{source[:12]}-" + "1" * 16,
            "source_main_sha": source,
            "architect_run_id": "123",
            "engineering_contract": {
                "enabled": True,
                "contract_id": "ENG-V6-TEST",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "Update src/qore/a.py safely.",
                "scope": ["src/qore/a.py"],
                "acceptance": ["tests/test_a.py remains green"],
                "required_tests": ["tests/test_a.py"],
                "forbidden": ["no Production authority"],
            },
            "production_authority": False,
        }

    @staticmethod
    def green_qg(self_tools):
        self_tools.quality_runs += 1
        self_tools.last_quality_success = True
        return {"success": True, "run_number": self_tools.quality_runs, "results": []}

    @patch.object(target, "_response_call")
    def test_one_call_patch_then_green_qg_is_ready(self, model_call):
        temp, root, source = self.repo()
        self.addCleanup(temp.cleanup)
        patch_text = (
            "diff --git a/src/qore/a.py b/src/qore/a.py\n"
            "--- a/src/qore/a.py\n"
            "+++ b/src/qore/a.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        )
        model_call.return_value = ({
            "action": "PATCH",
            "patch": patch_text,
            "evidence_requests": [],
            "summary": "bounded fix",
            "notes": [],
        }, "resp-1")
        with patch.object(target.v2.LocalTools, "run_quality_gate", target.CodexWorkerV6Tests.green_qg):
            final, usage = target.execute_v6(
                key="fake",
                repo=root,
                request=self.request(source),
                charter="charter",
            )
        self.assertEqual(final["status"], "READY")
        self.assertEqual(usage["model_calls"], 1)
        self.assertEqual((root / "src/qore/a.py").read_text(), "VALUE = 2\n")
        self.assertFalse(final["production_authority"])

    def test_patch_outside_allowlist_is_rejected(self):
        temp, root, source = self.repo()
        self.addCleanup(temp.cleanup)
        tools = target.v2.LocalTools(root)
        patch_text = (
            "diff --git a/README.md b/README.md\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/README.md\n"
            "@@ -0,0 +1 @@\n"
            "+x\n"
        )
        with self.assertRaisesRegex(target.v2.WorkerError, "outside controller allowlist"):
            target._apply_allowlisted_patch(tools, patch_text, allowlist=("src/qore/a.py",))

    def test_concrete_symbol_evidence_request_is_resolved(self):
        temp, root, _ = self.repo()
        self.addCleanup(temp.cleanup)
        (root / "src/qore/a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        evidence = target.resolve_evidence_requests(root, ["symbol:src/qore/a.py#f"])
        self.assertEqual(evidence[0]["kind"], "symbol")
        self.assertIn("return 1", evidence[0]["evidence"]["content"])

    def test_need_evidence_repeated_after_single_continuation_blocks(self):
        temp, root, source = self.repo()
        self.addCleanup(temp.cleanup)
        responses = [
            ({
                "action": "NEED_EVIDENCE",
                "patch": "",
                "evidence_requests": ["file:src/qore/a.py"],
                "summary": "need exact file",
                "notes": [],
            }, "resp-1"),
            ({
                "action": "NEED_EVIDENCE",
                "patch": "",
                "evidence_requests": ["test:tests/test_a.py"],
                "summary": "need more",
                "notes": [],
            }, "resp-2"),
        ]
        with patch.object(target, "_response_call", side_effect=responses):
            final, usage = target.execute_v6(
                key="fake",
                repo=root,
                request=self.request(source),
                charter="charter",
            )
        self.assertEqual(final["status"], "BLOCKED")
        self.assertEqual(usage["model_calls"], 2)
        self.assertIn("single deterministic evidence continuation", final["summary"])

    def test_contract_paths_are_deterministic(self):
        source = "a" * 40
        paths = target.contract_paths(self.request(source)["engineering_contract"])
        self.assertEqual(paths, ("src/qore/a.py", "tests/test_a.py"))

    def test_invalid_evidence_request_syntax_fails_closed(self):
        temp, root, _ = self.repo()
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(target.v2.WorkerError, "evidence request"):
            target.resolve_evidence_requests(root, ["please search around"])


if __name__ == "__main__":
    unittest.main()
