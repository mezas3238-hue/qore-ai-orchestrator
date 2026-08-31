from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import codex_v6_scope_policy as target


class CodexV6ScopePolicyTests(unittest.TestCase):
    def contract(self):
        return {
            "objective": "Modify src/qore/a.py and add src/qore/new_file.py.",
            "scope": ["src/qore/a.py", "src/qore/new_file.py"],
            "acceptance": ["tests/test_a.py must stay green"],
            "required_tests": ["tests/test_a.py"],
            "forbidden": ["do not edit docs/security.md"],
        }

    def test_tests_named_only_for_acceptance_are_read_only(self):
        self.assertEqual(
            target.patch_paths(self.contract()),
            ("src/qore/a.py", "src/qore/new_file.py"),
        )
        self.assertIn("tests/test_a.py", target.evidence_paths(self.contract()))
        self.assertNotIn("tests/test_a.py", target.patch_paths(self.contract()))

    def test_missing_new_file_remains_in_write_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src/qore").mkdir(parents=True)
            (root / "src/qore/a.py").write_text("x = 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_a.py").write_text("def test_x(): pass\n", encoding="utf-8")
            evidence, allowlist = target.hardened_initial_evidence(root, self.contract(), None)
        self.assertIn("src/qore/new_file.py", allowlist)
        self.assertIn("src/qore/new_file.py", evidence["missing_contract_paths"])
        self.assertIn("tests/test_a.py", evidence["readable_evidence_paths"])
        self.assertNotIn("tests/test_a.py", evidence["model_write_allowlist"])

    def test_materialized_reference_paths_are_writable_for_exact_correction(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src/qore").mkdir(parents=True)
            (root / "src/qore/a.py").write_text("x = 1\n", encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests/test_a.py").write_text("def test_x(): pass\n", encoding="utf-8")
            evidence, allowlist = target.hardened_initial_evidence(
                root,
                {"objective": "Correct src/qore/a.py", "scope": ["src/qore/a.py"], "acceptance": [], "required_tests": [], "forbidden": []},
                {"changed_files": ["tests/test_a.py"]},
            )
        self.assertIn("tests/test_a.py", allowlist)
        self.assertEqual(evidence["policy"], "objective_scope_write__acceptance_tests_read_only_v1")


if __name__ == "__main__":
    unittest.main()
