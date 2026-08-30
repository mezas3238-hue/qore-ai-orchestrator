from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load_module("codex_worker_v2", "scripts/run_codex_engineer_worker_v2.py")
builder = load_module("codex_request_builder", "scripts/build_codex_engineering_request.py")
publisher = load_module("codex_publisher_v2", "scripts/publish_codex_candidate_v2.py")


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    return result.stdout


def make_repo() -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name)
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "QORE Test")
    run_git(repo, "config", "user.email", "qore-test@example.invalid")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "base")
    return temp, repo, run_git(repo, "rev-parse", "HEAD").strip()


class CodexWorkerV2Tests(unittest.TestCase):
    def test_safe_path_rejects_escape_and_git(self) -> None:
        temp, repo, _ = make_repo()
        self.addCleanup(temp.cleanup)
        with self.assertRaises(worker.WorkerError):
            worker.safe_path(repo, "../outside", must_exist=False)
        with self.assertRaises(worker.WorkerError):
            worker.safe_path(repo, ".git/config")

    def test_patch_rejects_symlink_and_parent_escape(self) -> None:
        symlink_patch = "diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+target\n"
        with self.assertRaises(worker.WorkerError):
            worker.validate_patch_paths(symlink_patch)
        escape = "--- a/src/example.py\n+++ b/../escape.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
        with self.assertRaises(worker.WorkerError):
            worker.validate_patch_paths(escape)

    def test_candidate_fingerprint_includes_untracked_bytes(self) -> None:
        temp, repo, _ = make_repo()
        self.addCleanup(temp.cleanup)
        base = worker.candidate_fingerprint(repo)
        path = repo / "tests" / "test_new.py"
        path.write_text("def test_new():\n    assert 1 == 1\n", encoding="utf-8")
        first = worker.candidate_fingerprint(repo)
        self.assertNotEqual(base, first)
        path.write_text("def test_new():\n    assert 2 == 2\n", encoding="utf-8")
        second = worker.candidate_fingerprint(repo)
        self.assertNotEqual(first, second)

    def test_apply_patch_marks_gate_stale(self) -> None:
        temp, repo, _ = make_repo()
        self.addCleanup(temp.cleanup)
        tools = worker.LocalTools(repo)
        tools.last_quality_success = True
        patch = "--- a/src/example.py\n+++ b/src/example.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
        result = tools.apply_patch(patch)
        self.assertTrue(result["applied"])
        self.assertFalse(tools.last_quality_success)
        self.assertEqual(worker.changed_files(repo), ["src/example.py"])

    def test_request_builder_restricts_worker_target(self) -> None:
        source = "a" * 40
        decision = {
            "schema_version": "qore.architect.decision.v1",
            "source_main_sha": source,
            "status": "ENGINEERING_TASK",
            "next_actor": "CODEX",
            "engineering_contract": {
                "enabled": True,
                "contract_id": "C-1",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "bounded fix",
                "scope": [],
                "acceptance": [],
                "required_tests": [],
                "forbidden": [],
            },
            "production_authority": False,
        }
        request = builder.build(decision, "123")
        self.assertEqual(request["source_main_sha"], source)
        decision["engineering_contract"]["target_repository"] = "mezas3238-hue/qore-deepseek-reviewer"
        with self.assertRaises(ValueError):
            builder.build(decision, "123")

    def test_publisher_requires_independent_controller_gate(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "src" / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
        request = {
            "schema_version": "qore.codex.engineering.request.v1",
            "source_main_sha": source,
            "engineering_contract": {
                "enabled": True,
                "contract_id": "C-1",
                "target_repository": "mezas3238-hue/qore-core",
                "objective": "bounded fix",
            },
            "production_authority": False,
        }
        result = {
            "schema_version": "qore.codex.worker.result.v1",
            "source_main_sha": source,
            "contract_id": "C-1",
            "status": "READY",
            "quality_gate_success": True,
            "production_authority": False,
        }
        bad_qg = {
            "schema_version": "qore.codex.controller.qg.v1",
            "ruff": "SUCCESS",
            "mypy": "SUCCESS",
            "pytest_coverage": "FAILURE",
            "production_authority": False,
        }
        with self.assertRaises(publisher.PublishError):
            publisher.validate(request, result, bad_qg, repo)
        good_qg = dict(bad_qg, pytest_coverage="SUCCESS")
        self.assertEqual(publisher.validate(request, result, good_qg, repo), (source, "C-1", "bounded fix"))

    def test_worker_source_binding_and_clean_start(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        request = {
            "schema_version": "qore.codex.engineering.request.v1",
            "source_main_sha": source,
            "engineering_contract": {
                "enabled": True,
                "contract_id": "C-2",
                "target_repository": "mezas3238-hue/qore-core",
            },
            "production_authority": False,
        }
        observed_source, _ = worker.validate_request(request, repo)
        self.assertEqual(observed_source, source)
        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(worker.WorkerError):
            worker.validate_request(request, repo)


if __name__ == "__main__":
    unittest.main()
