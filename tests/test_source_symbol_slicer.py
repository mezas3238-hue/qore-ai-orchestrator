from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import source_symbol_slicer as slicer


class SourceSymbolSlicerTests(unittest.TestCase):
    def test_extracts_exact_class_and_method_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "module.py"
            path.write_text(
                "class A:\n"
                "    def first(self):\n"
                "        return 1\n"
                "\n"
                "    def second(self):\n"
                "        return 2\n"
                "\n"
                "def outside():\n"
                "    return 3\n",
                encoding="utf-8",
            )
            method = slicer.slice_python_symbol(
                root=root, relative_path="module.py", symbol="A.second"
            )
            self.assertEqual(method["start_line"], 5)
            self.assertEqual(method["end_line"], 6)
            self.assertIn("def second", method["content"])
            self.assertNotIn("def first", method["content"])
            self.assertEqual(len(method["slice_sha256"]), 64)

    def test_package_digest_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("def x():\n    return 1\n", encoding="utf-8")
            requests = [{"path": "module.py", "symbol": "x"}]
            first = slicer.build_slice_package(root=root, requests=requests)
            second = slicer.build_slice_package(root=root, requests=requests)
            self.assertEqual(first["package_sha256"], second["package_sha256"])
            self.assertFalse(first["production_authority"])

    def test_missing_symbol_and_path_escape_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "module.py").write_text("def x():\n    return 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "symbol not found"):
                slicer.slice_python_symbol(
                    root=root, relative_path="module.py", symbol="missing"
                )
            with self.assertRaisesRegex(ValueError, "declared root"):
                slicer.slice_python_symbol(
                    root=root, relative_path="../escape.py", symbol="x"
                )


if __name__ == "__main__":
    unittest.main()
