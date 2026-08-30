from __future__ import annotations

import importlib.util
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


v3 = load_module("codex_worker_v3", "scripts/run_codex_engineer_worker_v3.py")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout)
    return result.stdout


def make_history() -> tuple[tempfile.TemporaryDirectory[str], Path, str, str]:
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "QORE Test")
    git(repo, "config", "user.email", "qore-test@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    source = git(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_reference.py").write_text("def test_reference():\n    assert True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "reference")
    reference = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "--detach", source)
    return temp, repo, source, reference


class CodexWorkerV3Tests(unittest.TestCase):
    def test_limits_are_not_increased(self) -> None:
        self.assertEqual(v3.MAX_TURNS, 16)
        self.assertEqual(v3.MAX_TOTAL_TOKENS, 120_000)
        self.assertFalse(hasattr(v3, "previous_response_id"))

    def test_stateless_projection_preserves_exact_cacheable_prefix(self) -> None:
        conversation = [
            {"role": "user", "content": [{"type": "input_text", "text": "contract"}]},
            {"type": "function_call", "name": "read_file", "call_id": "c1", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1", "output": "EXACT-OLD-OUTPUT"},
        ]
        before = v3.stable_conversation(conversation)
        conversation.extend(
            [
                {"type": "function_call", "name": "search_text", "call_id": "c2", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c2", "output": "NEW-OUTPUT"},
            ]
        )
        after = v3.stable_conversation(conversation)
        self.assertEqual(before, after[: len(before)])
        self.assertEqual(before[2]["output"], "EXACT-OLD-OUTPUT")
        self.assertIsNot(before[0], conversation[0])

    def test_contract_reference_allowlist_is_exact_deduplicated_and_bounded(self) -> None:
        source = "a" * 40
        first = "b" * 40
        second = "c" * 40
        contract = {
            "scope": [
                f"Preserve head {first} exactly.",
                f"Compare {second}; duplicate {first}.",
                f"Do not treat {source} as a historical reference.",
                "Reject short deadbeef and 41-char " + ("d" * 41) + ".",
            ]
        }
        self.assertEqual(v3.contract_reference_shas(contract, source), (first, second))

        too_many = {"scope": [f"ref {index:040x}" for index in range(1, v3.MAX_REFERENCE_SHAS + 2)]}
        with self.assertRaises(v3.v2.WorkerError):
            v3.contract_reference_shas(too_many, source)

    def test_reference_diff_reads_only_contract_allowlisted_local_commit(self) -> None:
        temp, repo, source, reference = make_history()
        self.addCleanup(temp.cleanup)
        tools = v3.LocalToolsV3(repo, source, (reference,))
        result = tools.reference_diff(reference, 20_000)
        self.assertEqual(result["source_main_sha"], source)
        self.assertEqual(result["reference_sha"], reference)
        self.assertEqual(result["changed_files"], ["src/example.py", "tests/test_reference.py"])
        self.assertIn("+VALUE = 2", result["diff"])
        self.assertIn("tests/test_reference.py", result["diff"])

        with self.assertRaisesRegex(v3.v2.WorkerError, "not explicitly allowlisted"):
            tools.reference_diff("d" * 40, 20_000)
        with self.assertRaisesRegex(v3.v2.WorkerError, "1000"):
            tools.reference_diff(reference, 999)

    def test_reference_diff_tool_is_read_only_and_finish_remains_terminal_tool(self) -> None:
        names = [item.get("name") for item in v3.TOOLS]
        self.assertEqual(names.count("reference_diff"), 1)
        self.assertEqual(names[-1], "finish")
        temp, repo, source, reference = make_history()
        self.addCleanup(temp.cleanup)
        before = git(repo, "status", "--porcelain")
        v3.LocalToolsV3(repo, source, (reference,)).reference_diff(reference, 10_000)
        after = git(repo, "status", "--porcelain")
        self.assertEqual(before, after)

    def test_trace_contains_no_arguments_or_tool_contents(self) -> None:
        trace: list[dict[str, object]] = []
        payload = {
            "usage": {
                "input_tokens": 100,
                "input_tokens_details": {"cached_tokens": 80, "cache_write_tokens": 0},
                "output_tokens": 10,
                "output_tokens_details": {"reasoning_tokens": 4},
                "total_tokens": 110,
            }
        }
        usage_total = {"input_tokens": 100, "cached_tokens": 80, "cache_write_tokens": 0, "output_tokens": 10}
        v3.append_trace(
            trace,
            turn=1,
            tool_name="read_file",
            payload=payload,
            usage_total=usage_total,
            tool_output_chars=1234,
        )
        self.assertEqual(trace[0]["tool"], "read_file")
        self.assertEqual(trace[0]["tool_output_chars"], 1234)
        self.assertNotIn("arguments", trace[0])
        self.assertNotIn("output", trace[0])
        self.assertNotIn("content", trace[0])

    def test_workflow_executes_v3_entrypoint(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "codex-engineer-worker.yml").read_text(encoding="utf-8")
        self.assertIn("Run bounded GPT-5.3-Codex engineering worker V3", workflow)
        self.assertIn("python3 scripts/run_codex_engineer_worker_v3.py", workflow)
        self.assertNotIn("python3 scripts/run_codex_engineer_worker_v2.py", workflow)


if __name__ == "__main__":
    unittest.main()
