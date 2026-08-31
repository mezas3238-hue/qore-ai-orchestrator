from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import fable_cost_preflight_v2 as fable_cost


class FableCostPreflightV2Tests(unittest.TestCase):
    def test_cold_cache_write_is_not_mispriced_as_standard_input(self):
        plan = fable_cost.FableAuditTokenPlanV2(
            stable_tokens=1_000_000,
            changed_tokens=100_000,
            cross_boundary_tokens=0,
            expected_output_tokens=20_000,
            cache_hit_ratio=0.0,
            cache_write_ratio=1.0,
        )
        card = eco.PriceCard(
            input_per_million=10.0,
            cached_input_per_million=1.0,
            cache_write_per_million=12.5,
            output_per_million=50.0,
        )
        estimate = fable_cost.preflight_fable_cost_v2(
            token_plan=plan,
            price_card=card,
            hard_budget_usd=20.0,
        )
        self.assertEqual(estimate.cache_write_tokens, 1_000_000)
        self.assertEqual(estimate.cache_hit_tokens, 0)
        self.assertEqual(estimate.standard_input_tokens, 100_000)
        self.assertAlmostEqual(estimate.pre_discount_usd, 14.5)
        self.assertAlmostEqual(estimate.estimated_usd, 14.5)
        self.assertTrue(estimate.within_budget)

    def test_warm_cache_and_batch_discount_reduce_estimate_explicitly(self):
        plan = fable_cost.FableAuditTokenPlanV2(
            stable_tokens=1_000_000,
            changed_tokens=100_000,
            cross_boundary_tokens=0,
            expected_output_tokens=20_000,
            cache_hit_ratio=0.9,
            cache_write_ratio=0.0,
            batch_discount=0.5,
        )
        card = eco.PriceCard(10.0, 1.0, 12.5, 50.0)
        estimate = fable_cost.preflight_fable_cost_v2(
            token_plan=plan,
            price_card=card,
            hard_budget_usd=2.0,
        )
        self.assertEqual(estimate.cache_hit_tokens, 900_000)
        self.assertEqual(estimate.standard_input_tokens, 200_000)
        self.assertAlmostEqual(estimate.pre_discount_usd, 3.9)
        self.assertAlmostEqual(estimate.estimated_usd, 1.95)
        self.assertTrue(estimate.within_budget)

    def test_invalid_cache_partition_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            fable_cost.FableAuditTokenPlanV2(
                stable_tokens=100,
                changed_tokens=0,
                cross_boundary_tokens=0,
                expected_output_tokens=0,
                cache_hit_ratio=0.8,
                cache_write_ratio=0.3,
            )


if __name__ == "__main__":
    unittest.main()
