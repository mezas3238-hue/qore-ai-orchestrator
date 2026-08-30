from __future__ import annotations

import hashlib
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

    def test_spend_equivalent_budget_does_not_treat_cached_replay_as_full_price(self) -> None:
        historical = {
            "input_tokens": 124147,
            "cached_tokens": 91520,
            "cache_write_tokens": 0,
            "output_tokens": 778,
            "total_tokens": 124925,
        }
        self.assertGreater(historical["total_tokens"], worker.MAX_TOTAL_TOKENS)
        self.assertEqual(worker.spend_equivalent_tokens(historical), 48003)
        self.assertLess(worker.spend_equivalent_tokens(historical), worker.MAX_TOTAL_TOKENS)

    def test_spend_equivalent_budget_uses_output_and_cache_write_reserves(self) -> None:
        usage = {
            "input_tokens": 120,
            "cached_tokens": 20,
            "cache_write_tokens": 20,
            "output_tokens": 10,
        }
        # 80 uncached + 2 cached-equivalent + 25 cache-write-equivalent + 80 output-equivalent.
        self.assertEqual(worker.spend_equivalent_tokens(usage), 187)
        with self.assertRaises(worker.WorkerError):
            worker.spend_equivalent_tokens(
                {"input_tokens": 10, "cached_tokens": 9, "cache_write_tokens": 2, "output_tokens": 0}
            )

    def test_old_tool_outputs_are_compacted_without_mutating_canonical_history(self) -> None:
        old_output = json.dumps({"ok": True, "result": {"content": "OLD-SENSITIVE-CONTEXT" * 200}})
        middle_output = json.dumps({"ok": True, "result": {"content": "middle"}})
        latest_output = json.dumps({"ok": True, "result": {"content": "latest"}})
        conversation = [
            {"role": "user", "content": [{"type": "input_text", "text": "contract"}]},
            {"type": "reasoning", "id": "r1", "summary": []},
            {"type": "function_call", "name": "read_file", "call_id": "c1", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": old_output},
            {"type": "function_call", "name": "search_text", "call_id": "c2", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c2", "output": middle_output},
            {"type": "function_call", "name": "git_diff", "call_id": "c3", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c3", "output": latest_output},
        ]
        projected = worker.compact_conversation(conversation, keep_full=2)
        compacted = json.loads(projected[3]["output"])
        self.assertTrue(compacted["qore_compacted_tool_output"])
        self.assertEqual(compacted["sha256"], hashlib.sha256(old_output.encode()).hexdigest())
        self.assertEqual(compacted["original_chars"], len(old_output))
        self.assertNotIn("OLD-SENSITIVE-CONTEXT", projected[3]["output"])
        self.assertEqual(projected[5]["output"], middle_output)
        self.assertEqual(projected[7]["output"], latest_output)
        self.assertEqual(conversation[3]["output"], old_output)
        self.assertEqual(worker.compact_conversation(conversation, keep_full=2), projected)

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
