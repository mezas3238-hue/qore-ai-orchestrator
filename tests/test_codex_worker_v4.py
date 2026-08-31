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
import run_codex_engineer_worker_v4 as v4


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


def make_descendant_history() -> tuple[tempfile.TemporaryDirectory[str], Path, str, str]:
    temp = tempfile.TemporaryDirectory()
    repo = Path(temp.name)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "QORE Test")
    git(repo, "config", "user.email", "qore-test@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "evidence.md").write_text("baseline\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "source")
    source = git(repo, "rev-parse", "HEAD").strip()

    (repo / "src" / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_example.py").write_text(
        "def test_example():\n    assert True\n", encoding="utf-8"
    )
    (repo / "docs" / "evidence.md").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "historical candidate")
    reference = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "--detach", source)
    return temp, repo, source, reference


class CodexWorkerV4Tests(unittest.TestCase):
    def test_v4_does_not_increase_model_limits_or_remote_state(self) -> None:
        self.assertEqual(v3.MAX_TURNS, 16)
        self.assertEqual(v3.MAX_TOTAL_TOKENS, 120_000)
        self.assertEqual(v4.WORKER_VERSION, "v4")
        self.assertIn("single-allowlisted-descendant", v4.REFERENCE_MATERIALIZATION_POLICY)

    def test_materialization_requires_explicit_objective_and_one_reference(self) -> None:
        source = "a" * 40
        reference = "b" * 40
        ordinary = {"objective": f"Compare exact historical head {reference} read-only."}
        self.assertIsNone(v4.required_materialized_reference(ordinary, source))

        cumulative = {
            "objective": f"Create a cumulative replacement candidate by checking out exact head {reference}."
        }
        self.assertEqual(v4.required_materialized_reference(cumulative, source), reference)

        ambiguous = {
            "objective": (
                "Create a cumulative replacement candidate by checking out exact references "
                + ("b" * 40)
                + " and "
                + ("c" * 40)
                + "."
            )
        }
        with self.assertRaisesRegex(v2.WorkerError, "exactly one"):
            v4.required_materialized_reference(ambiguous, source)

        missing = {"objective": "Create a cumulative replacement candidate from an unspecified head."}
        with self.assertRaisesRegex(v2.WorkerError, "exactly one"):
            v4.required_materialized_reference(missing, source)

    def test_materialize_reference_delta_is_exact_for_modify_add_delete(self) -> None:
        temp, repo, source, reference = make_descendant_history()
        self.addCleanup(temp.cleanup)

        evidence = v4.materialize_reference_delta(repo, source, reference, (reference,))
        self.assertEqual(evidence["source_main_sha"], source)
        self.assertEqual(evidence["reference_sha"], reference)
        self.assertEqual(evidence["merge_base_sha"], source)
        self.assertEqual(
            evidence["changed_files"],
            ["docs/evidence.md", "src/example.py", "tests/test_example.py"],
        )
        self.assertEqual((repo / "src" / "example.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertFalse((repo / "docs" / "evidence.md").exists())
        self.assertEqual(
            (repo / "tests" / "test_example.py").read_text(encoding="utf-8"),
            "def test_example():\n    assert True\n",
        )
        self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), source)
        self.assertEqual(v2.changed_files(repo), evidence["changed_files"])

    def test_materialization_rejects_unallowlisted_dirty_and_non_descendant_reference(self) -> None:
        temp, repo, source, reference = make_descendant_history()
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(v2.WorkerError, "not allowlisted"):
            v4.materialize_reference_delta(repo, source, reference, ())

        (repo / "src" / "example.py").write_text("DIRTY\n", encoding="utf-8")
        with self.assertRaisesRegex(v2.WorkerError, "clean source"):
            v4.materialize_reference_delta(repo, source, reference, (reference,))
        git(repo, "reset", "--hard", source)

        git(repo, "checkout", "--orphan", "unrelated")
        for child in list(repo.iterdir()):
            if child.name != ".git":
                if child.is_dir():
                    import shutil
                    shutil.rmtree(child)
                else:
                    child.unlink()
        (repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "unrelated")
        unrelated = git(repo, "rev-parse", "HEAD").strip()
        git(repo, "checkout", "--detach", source)
        with self.assertRaises(v2.WorkerError):
            v4.materialize_reference_delta(repo, source, unrelated, (unrelated,))

    def test_localtools_auto_materializes_only_selected_allowlisted_reference(self) -> None:
        temp, repo, source, reference = make_descendant_history()
        self.addCleanup(temp.cleanup)
        old_auto = v4._AUTO_REFERENCE_SHA
        old_evidence = v4._MATERIALIZATION_EVIDENCE
        try:
            v4._AUTO_REFERENCE_SHA = reference
            v4._MATERIALIZATION_EVIDENCE = None
            tools = v4.LocalToolsV4(repo, source, (reference,))
            self.assertEqual(v2.changed_files(repo), ["docs/evidence.md", "src/example.py", "tests/test_example.py"])
            self.assertIsNotNone(v4._MATERIALIZATION_EVIDENCE)
            self.assertFalse(tools.last_quality_success)
        finally:
            v4._AUTO_REFERENCE_SHA = old_auto
            v4._MATERIALIZATION_EVIDENCE = old_evidence

    def test_augmented_charter_tells_codex_not_to_reconstruct_materialized_delta(self) -> None:
        reference = "b" * 40
        value = v4._augment_charter("BASE", reference)
        self.assertIn(reference, value)
        self.assertIn("already materialized", value)
        self.assertIn("Do NOT reconstruct", value)
        self.assertIn("current git diff as the cumulative baseline", value)
        self.assertEqual(v4._augment_charter("BASE", None), "BASE")

    def test_usage_annotation_contains_policy_but_no_reference_diff_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.json"
            path.write_text(json.dumps({"worker_version": "v3", "turn_trace": []}) + "\n")
            old = v4._MATERIALIZATION_EVIDENCE
            try:
                v4._MATERIALIZATION_EVIDENCE = {
                    "source_main_sha": "a" * 40,
                    "reference_sha": "b" * 40,
                    "merge_base_sha": "a" * 40,
                    "changed_files": ["src/example.py"],
                    "delta_sha256": "c" * 64,
                }
                v4._annotate_usage(path, "b" * 40)
            finally:
                v4._MATERIALIZATION_EVIDENCE = old
            value = json.loads(path.read_text())
            self.assertEqual(value["worker_version"], "v4")
            self.assertEqual(value["materialized_reference_sha"], "b" * 40)
            self.assertNotIn("diff", value["materialization_evidence"])
            self.assertNotIn("content", value["materialization_evidence"])

    def test_v4_runtime_remains_available_while_live_workflow_advances(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "codex-engineer-worker.yml").read_text(encoding="utf-8")
        self.assertIn("Run bounded GPT-5.3-Codex engineering worker V5", workflow)
        self.assertIn("python3 scripts/run_codex_engineer_worker_v5.py", workflow)
        self.assertNotIn("python3 scripts/run_codex_engineer_worker_v4.py", workflow)
        self.assertTrue((ROOT / "scripts" / "run_codex_engineer_worker_v4.py").is_file())


if __name__ == "__main__":
    unittest.main()
