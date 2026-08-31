from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import unified_budget_shadow as budget


class UnifiedBudgetShadowTests(unittest.TestCase):
    def test_external_reviewers_are_included_in_end_to_end_budget(self):
        result = budget.assess_unified_budget_shadow(
            orchestrator_receipt={"estimated_spend_usd": 1.632235},
            reviewer_costs=[
                {"reviewer": "DEEPSEEK_EXPERT", "cost_usd": 0.10},
                {"reviewer": "DEEPSEEK_CODER", "cost_usd": 0.10},
                {"reviewer": "CLAUDE", "cost_usd": 0.64},
            ],
            fable_costs=[],
            hard_budget_usd=5.0,
            mandatory_review_pending=False,
        )
        self.assertAlmostEqual(result.total_end_to_end_spend_usd, 2.472235)
        self.assertAlmostEqual(result.external_reviewer_spend_usd, 0.84)
        self.assertEqual(result.action, "WITHIN_BUDGET")
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.production_authority)

    def test_budget_exhaustion_never_authorizes_skipping_mandatory_review(self):
        result = budget.assess_unified_budget_shadow(
            orchestrator_receipt={"estimated_spend_usd": 4.8},
            reviewer_costs=[{"reviewer": "CLAUDE", "cost_usd": 0.5}],
            fable_costs=[],
            hard_budget_usd=5.0,
            mandatory_review_pending=True,
        )
        self.assertTrue(result.would_exceed_budget)
        self.assertEqual(
            result.action,
            "STOP_AND_ESCALATE_BUDGET_WITHOUT_SKIPPING_MANDATORY_REVIEW",
        )

    def test_fable_cost_is_part_of_same_economic_truth(self):
        result = budget.assess_unified_budget_shadow(
            orchestrator_receipt={"estimated_spend_usd": 1.0},
            reviewer_costs=[],
            fable_costs=[{"audit": "FULL_SYSTEM", "cost_usd": 3.0}],
            hard_budget_usd=5.0,
            mandatory_review_pending=False,
        )
        self.assertEqual(result.fable_spend_usd, 3.0)
        self.assertEqual(result.total_end_to_end_spend_usd, 4.0)


if __name__ == "__main__":
    unittest.main()
