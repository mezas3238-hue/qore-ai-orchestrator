from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_reviewer_economics as reviewer_economics


class AnalyzeReviewerEconomicsTests(unittest.TestCase):
    def test_unique_yield_overlap_and_false_positives_are_separate(self):
        result = reviewer_economics.analyze_reviewer_economics(
            findings=[
                {
                    "reviewer": "DEEPSEEK_EXPERT",
                    "finding_id": "F1",
                    "fingerprint": "authority-gap",
                    "material": True,
                    "adjudication": "VALID",
                    "severity": "HIGH",
                },
                {
                    "reviewer": "CLAUDE",
                    "finding_id": "F2",
                    "fingerprint": "authority-gap",
                    "material": True,
                    "adjudication": "VALID",
                    "severity": "HIGH",
                },
                {
                    "reviewer": "DEEPSEEK_EXPERT",
                    "finding_id": "F3",
                    "fingerprint": "import-gap",
                    "material": True,
                    "adjudication": "VALID",
                    "severity": "MEDIUM",
                },
                {
                    "reviewer": "CLAUDE",
                    "finding_id": "F4",
                    "fingerprint": "noise",
                    "material": False,
                    "adjudication": "FALSE_POSITIVE",
                    "severity": "LOW",
                },
            ],
            reviewer_costs=[
                {"reviewer": "DEEPSEEK_EXPERT", "cost_usd": 0.10},
                {"reviewer": "CLAUDE", "cost_usd": 0.60},
            ],
        )
        rows = {row["reviewer"]: row for row in result["reviewers"]}
        self.assertEqual(rows["DEEPSEEK_EXPERT"]["distinct_valid_material_fingerprints"], 2)
        self.assertEqual(rows["DEEPSEEK_EXPERT"]["unique_valid_material_findings"], 1)
        self.assertEqual(rows["CLAUDE"]["unique_valid_material_findings"], 0)
        self.assertEqual(rows["CLAUDE"]["false_positives"], 1)
        self.assertEqual(
            result["cross_reviewer_overlap"],
            [{"fingerprint": "authority-gap", "reviewers": ["CLAUDE", "DEEPSEEK_EXPERT"]}],
        )
        self.assertIn("DO_NOT_SUPPRESS_REVIEWERS", result["policy"])
        self.assertFalse(result["production_authority"])

    def test_invalid_adjudication_and_negative_cost_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "adjudication"):
            reviewer_economics.analyze_reviewer_economics(
                findings=[
                    {
                        "reviewer": "X",
                        "finding_id": "F",
                        "fingerprint": "fp",
                        "material": True,
                        "adjudication": "PASS_BY_INFERENCE",
                    }
                ],
                reviewer_costs=[],
            )
        with self.assertRaisesRegex(ValueError, "cost_usd"):
            reviewer_economics.analyze_reviewer_economics(
                findings=[], reviewer_costs=[{"reviewer": "X", "cost_usd": -1}]
            )


if __name__ == "__main__":
    unittest.main()
