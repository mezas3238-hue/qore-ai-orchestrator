from __future__ import annotations

import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import review_verdict_evidence as target


class ReviewVerdictEvidenceTests(unittest.TestCase):
    HEAD = "a" * 40
    PACKAGE = "QORE-SOL-aaaaaaaaaaaa-DS-EXPERT-R123"

    def test_clean_contract_is_unambiguous(self):
        verdict, markers = target.classify_review_text("HALLAZGOS: NINGUNO\nVALIDACIÓN OK\n")
        self.assertIs(verdict, target.VerdictClass.CLEAN)
        self.assertTrue(markers)

    def test_blocked_is_not_clean(self):
        verdict, _ = target.classify_review_text("EVIDENCIA INSUFICIENTE / VALIDACIÓN BLOQUEADA")
        self.assertIs(verdict, target.VerdictClass.BLOCKED)

    def test_conflicting_markers_are_ambiguous(self):
        verdict, _ = target.classify_review_text(
            "HALLAZGOS: NINGUNO\nVALIDACIÓN OK\nVALIDACIÓN NO OK"
        )
        self.assertIs(verdict, target.VerdictClass.AMBIGUOUS)

    def test_exact_deepseek_review_binding(self):
        body = (
            f"<!-- QORE-DEEPSEEK-REVIEW package={self.PACKAGE} head={self.HEAD} -->\n\n"
            "HALLAZGOS: NINGUNO\nVALIDACIÓN OK\n"
        )
        evidence = target.deepseek_review_from_pr_reviews(
            reviews=[{"body": body, "commit_id": self.HEAD}],
            package_id=self.PACKAGE,
            expected_head=self.HEAD,
        )
        self.assertIs(evidence.verdict, target.VerdictClass.CLEAN)
        self.assertFalse(evidence.production_authority)

    def test_deepseek_wrong_commit_fails_closed(self):
        body = (
            f"<!-- QORE-DEEPSEEK-REVIEW package={self.PACKAGE} head={self.HEAD} -->\n"
            "HALLAZGOS: NINGUNO\nVALIDACIÓN OK\n"
        )
        with self.assertRaisesRegex(ValueError, "commit_id"):
            target.deepseek_review_from_pr_reviews(
                reviews=[{"body": body, "commit_id": "b" * 40}],
                package_id=self.PACKAGE,
                expected_head=self.HEAD,
            )

    def test_claude_artifact_is_parsed(self):
        memory = io.BytesIO()
        with zipfile.ZipFile(memory, "w") as archive:
            archive.writestr("nested/claude-review.md", "HALLAZGOS: NINGUNO\nVALIDACIÓN OK\n")
        evidence = target.claude_review_from_artifact(
            archive_bytes=memory.getvalue(),
            package_id="QORE-SOL-aaaaaaaaaaaa-CLAUDE-R123",
            expected_head=self.HEAD,
        )
        self.assertIs(evidence.verdict, target.VerdictClass.CLEAN)


if __name__ == "__main__":
    unittest.main()
