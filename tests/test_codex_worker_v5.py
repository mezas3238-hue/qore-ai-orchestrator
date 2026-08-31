from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v3 as v3
import run_codex_engineer_worker_v5 as v5


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


def make_repo() -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "QORE Test")
    git(repo, "config", "user.email", "qore-test@example.invalid")
    (repo / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "source")
    return temp, repo, git(repo, "rev-parse", "HEAD").strip()


PATCH = """diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1 +1 @@
-VALUE = 1
+VALUE = 2
"""


class GreenTools(v5.LocalToolsV5):
    def run_quality_gate(self) -> dict[str, object]:
        self.quality_runs += 1
        self.last_quality_success = True
        return {
            "success": True,
            "run_number": self.quality_runs,
            "results": [
                {"command": "ruff check .", "returncode": 0, "output": "secret-like output omitted"},
                {"command": "mypy src tests", "returncode": 0, "output": "ok"},
                {"command": "pytest --cov=src/qore --cov-report=term-missing", "returncode": 0, "output": "ok"},
            ],
        }


class RedTools(v5.LocalToolsV5):
    def run_quality_gate(self) -> dict[str, object]:
        self.quality_runs += 1
        self.last_quality_success = False
        return {
            "success": False,
            "run_number": self.quality_runs,
            "results": [{"command": "ruff check .", "returncode": 1, "output": "failure details"}],
        }


class CodexWorkerV5Tests(unittest.TestCase):
    def setUp(self) -> None:
        v5._TERMINAL_QG_EVIDENCE = None

    def test_v5_keeps_existing_model_caps_and_cache_key(self) -> None:
        self.assertEqual(v5.MAX_TURNS, 16)
        self.assertEqual(v5.MAX_TOTAL_TOKENS, 120_000)
        self.assertEqual(v5.PROMPT_CACHE_KEY, "qore-codex-engineer-worker-v4")
        self.assertIn("zero additional model", v5.__doc__.casefold())

    def test_budget_edge_after_successful_patch_can_be_controller_ready(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        tools = GreenTools(repo, source, ())
        applied = v5.dispatch_tool_v5(tools, "apply_patch", {"patch": PATCH})
        self.assertTrue(applied["applied"])
        self.assertEqual(tools.last_bounded_action, "apply_patch")

        result = v5.terminal_budget_result(
            repo,
            source,
            "CONTRACT-1",
            tools,
            16,
            "budget edge",
        )
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["quality_gate_success"])
        self.assertEqual(result["quality_gate_runs"], 1)
        self.assertEqual(result["changed_files"], ["example.py"])
        self.assertFalse(result["production_authority"])
        evidence = v5._TERMINAL_QG_EVIDENCE
        self.assertIsInstance(evidence, dict)
        assert isinstance(evidence, dict)
        self.assertTrue(evidence["no_additional_model_call"])
        self.assertTrue(evidence["candidate_unchanged_by_qg"])
        self.assertTrue(evidence["quality_gate_success"])
        self.assertNotIn("output", json.dumps(evidence))

    def test_any_intervening_tool_action_disables_terminal_qg(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        tools = GreenTools(repo, source, ())
        v5.dispatch_tool_v5(tools, "apply_patch", {"patch": PATCH})
        v5.dispatch_tool_v5(
            tools,
            "read_file",
            {"path": "example.py", "start_line": 1, "end_line": 1},
        )
        result = v5.terminal_budget_result(repo, source, "CONTRACT-2", tools, 16, "budget edge")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(tools.quality_runs, 0)
        self.assertIsNone(v5._TERMINAL_QG_EVIDENCE)

    def test_rejected_patch_does_not_get_terminal_qg(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        tools = GreenTools(repo, source, ())
        rejected = v5.dispatch_tool_v5(
            tools,
            "apply_patch",
            {"patch": PATCH.replace("VALUE = 1", "VALUE = 999")},
        )
        self.assertFalse(rejected["applied"])
        result = v5.terminal_budget_result(repo, source, "CONTRACT-3", tools, 16, "budget edge")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(tools.quality_runs, 0)

    def test_red_terminal_qg_remains_blocked(self) -> None:
        temp, repo, source = make_repo()
        self.addCleanup(temp.cleanup)
        tools = RedTools(repo, source, ())
        v5.dispatch_tool_v5(tools, "apply_patch", {"patch": PATCH})
        result = v5.terminal_budget_result(repo, source, "CONTRACT-4", tools, 16, "budget edge")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["quality_gate_success"])
        self.assertEqual(result["quality_gate_runs"], 1)
        self.assertFalse(result["production_authority"])

    def test_usage_annotation_is_bounded_and_contains_no_qg_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            path.write_text(json.dumps({"worker_version": "v4", "turn_trace": []}) + "\n")
            v5._TERMINAL_QG_EVIDENCE = {
                "policy": v5.TERMINAL_QG_POLICY,
                "no_additional_model_call": True,
                "commands": [{"command": "ruff check .", "returncode": 0}],
                "production_authority": False,
            }
            v5._annotate_usage(path)
            value = json.loads(path.read_text())
            self.assertEqual(value["worker_version"], "v5")
            self.assertEqual(value["terminal_qg_policy"], v5.TERMINAL_QG_POLICY)
            self.assertTrue(value["terminal_qg_evidence"]["no_additional_model_call"])
            self.assertNotIn("output", json.dumps(value["terminal_qg_evidence"]))

    def test_live_workflow_routes_to_v5_and_keeps_independent_controller_qg(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "codex-engineer-worker.yml").read_text(encoding="utf-8")
        self.assertIn("Run bounded GPT-5.3-Codex engineering worker V5", workflow)
        self.assertIn("python3 scripts/run_codex_engineer_worker_v5.py", workflow)
        self.assertNotIn("python3 scripts/run_codex_engineer_worker_v4.py", workflow)
        self.assertIn("Controller reruns Ruff on READY candidate", workflow)
        self.assertIn("Controller reruns Mypy on READY candidate", workflow)
        self.assertIn("Controller reruns Pytest coverage on READY candidate", workflow)


if __name__ == "__main__":
    unittest.main()
