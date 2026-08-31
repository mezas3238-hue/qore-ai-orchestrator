from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import audit_polling_patterns as polling


class AuditPollingPatternsTests(unittest.TestCase):
    def test_reports_evidence_without_declaring_defect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "worker.py").write_text(
                "import time\n"
                "time.sleep(1)\n"
                "# polling marker\n",
                encoding="utf-8",
            )
            result = polling.audit_paths(root=root, paths=["worker.py"])
            kinds = {hit["kind"] for hit in result["hits"]}
            self.assertIn("PYTHON_SLEEP", kinds)
            self.assertIn("POLLING_TERM", kinds)
            self.assertEqual(
                result["interpretation"], "EVIDENCE_ONLY_NOT_AUTOMATIC_DEFECT"
            )
            self.assertFalse(result["production_authority"])

    def test_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "under root"):
                polling.audit_paths(root=Path(directory), paths=["../escape.py"])


if __name__ == "__main__":
    unittest.main()
