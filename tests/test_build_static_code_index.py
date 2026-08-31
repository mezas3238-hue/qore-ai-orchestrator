from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_static_code_index as index


class BuildStaticCodeIndexTests(unittest.TestCase):
    def test_index_and_reverse_impact_closure_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pkg").mkdir()
            (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "pkg" / "a.py").write_text(
                "class A:\n    def run(self):\n        return 1\n",
                encoding="utf-8",
            )
            (root / "pkg" / "b.py").write_text(
                "from pkg.a import A\n\ndef use():\n    return A()\n",
                encoding="utf-8",
            )
            (root / "pkg" / "c.py").write_text(
                "from pkg.b import use\n\ndef call():\n    return use()\n",
                encoding="utf-8",
            )
            result = index.build_static_index(
                root=root,
                paths=["pkg/__init__.py", "pkg/a.py", "pkg/b.py", "pkg/c.py"],
            )
            edges = {(edge["from"], edge["to"]) for edge in result["local_dependency_edges"]}
            self.assertIn(("pkg/b.py", "pkg/a.py"), edges)
            self.assertIn(("pkg/c.py", "pkg/b.py"), edges)
            impacted = index.reverse_impact_closure(
                changed_paths=["pkg/a.py"],
                local_dependency_edges=result["local_dependency_edges"],
            )
            self.assertEqual(impacted, ("pkg/a.py", "pkg/b.py", "pkg/c.py"))
            a = next(row for row in result["files"] if row["path"] == "pkg/a.py")
            self.assertIn("A", a["symbols"])
            self.assertIn("A.run", a["symbols"])
            self.assertEqual(len(result["index_sha256"]), 64)
            self.assertFalse(result["production_authority"])

    def test_dynamic_import_limit_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("import importlib\n", encoding="utf-8")
            result = index.build_static_index(root=root, paths=["a.py"])
            self.assertIn("dynamic imports and runtime dependency injection require separate evidence", result["limitations"])

    def test_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "under root"):
                index.build_static_index(root=Path(directory), paths=["../escape.py"])


if __name__ == "__main__":
    unittest.main()
