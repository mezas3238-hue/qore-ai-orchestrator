from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_candidate_binding as binding


class BuildCandidateBindingTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    def repository(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        self.git(root, "init")
        self.git(root, "config", "user.email", "test@example.com")
        self.git(root, "config", "user.name", "QORE Test")
        (root / "file.txt").write_text("base\n", encoding="utf-8")
        self.git(root, "add", "file.txt")
        self.git(root, "commit", "-m", "base")
        base = self.git(root, "rev-parse", "HEAD")
        self.git(root, "checkout", "-b", "feature")
        (root / "file.txt").write_text("base\nhead\n", encoding="utf-8")
        self.git(root, "add", "file.txt")
        self.git(root, "commit", "-m", "head")
        head = self.git(root, "rev-parse", "HEAD")
        self.git(root, "checkout", "master")
        self.git(root, "merge", "--no-ff", "feature", "-m", "synthetic")
        synthetic = self.git(root, "rev-parse", "HEAD")
        return temp, root, base, head, synthetic

    def test_builds_exact_binding_and_tree(self):
        temp, root, base, head, synthetic = self.repository()
        try:
            result = binding.build_candidate_binding(
                root=root,
                repository="mezas3238-hue/qore-core",
                base_sha=base,
                head_sha=head,
                synthetic_sha=synthetic,
            )
            self.assertEqual(result["synthetic_parents"], [base, head])
            self.assertEqual(result["tree_sha"], self.git(root, "show", "-s", "--format=%T", head))
            self.assertTrue(result["base_is_ancestor_of_head"])
            self.assertTrue(result["candidate_id"].startswith("QORE-CAND-"))
            self.assertFalse(result["production_authority"])
        finally:
            temp.cleanup()

    def test_wrong_synthetic_binding_fails_closed(self):
        temp, root, base, head, synthetic = self.repository()
        try:
            with self.assertRaisesRegex(ValueError, "parents"):
                binding.build_candidate_binding(
                    root=root,
                    repository="mezas3238-hue/qore-core",
                    base_sha=base,
                    head_sha=head,
                    synthetic_sha=head,
                )
        finally:
            temp.cleanup()

    def test_unavailable_object_fails_closed(self):
        temp, root, base, head, synthetic = self.repository()
        try:
            with self.assertRaisesRegex(ValueError, "not available"):
                binding.build_candidate_binding(
                    root=root,
                    repository="mezas3238-hue/qore-core",
                    base_sha=base,
                    head_sha="f" * 40,
                    synthetic_sha=synthetic,
                )
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
