from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_recertification as target


class EconomicRecertificationTests(unittest.TestCase):
    def evidence(self, **overrides):
        values = dict(
            replay_cases=12,
            replay_mismatches=0,
            shadow_completion_observations=12,
            shadow_decision_mismatches=0,
            material_findings_baseline=3,
            material_findings_preserved=3,
            post_merge_escape_defects=0,
            baseline_usd=2.0,
            optimized_projected_usd=0.7,
            reviewer_suppression_enabled=False,
            final_sol_required=True,
            production_authority=False,
        )
        values.update(overrides)
        return target.RecertificationEvidence(**values)

    def test_clean_nonregression_passes(self):
        result = target.recertify(
            self.evidence(),
            minimum_replay_cases=10,
            minimum_shadow_observations=10,
            minimum_savings_ratio=0.5,
        )
        self.assertTrue(result.passed)
        self.assertGreater(result.savings_ratio, 0.5)
        self.assertFalse(result.production_authority)

    def test_reviewer_suppression_blocks(self):
        result = target.recertify(self.evidence(reviewer_suppression_enabled=True))
        self.assertFalse(result.passed)
        self.assertIn("REVIEWER_SUPPRESSION_NOT_RECERTIFIED", result.blockers)

    def test_finding_regression_blocks(self):
        result = target.recertify(self.evidence(material_findings_preserved=2))
        self.assertIn("MATERIAL_FINDING_DETECTION_REGRESSION", result.blockers)

    def test_decision_mismatch_blocks(self):
        result = target.recertify(self.evidence(shadow_decision_mismatches=1))
        self.assertIn("SHADOW_DECISION_MISMATCH", result.blockers)

    def test_no_cost_improvement_blocks(self):
        result = target.recertify(self.evidence(optimized_projected_usd=2.1))
        self.assertIn("NO_ECONOMIC_IMPROVEMENT", result.blockers)

    def test_production_authority_is_rejected_at_input(self):
        with self.assertRaisesRegex(ValueError, "production_authority"):
            self.evidence(production_authority=True)


if __name__ == "__main__":
    unittest.main()
