from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import fable_audit_cost_simulator as simulator


class FableAuditCostSimulatorTests(unittest.TestCase):
    def test_deduplicates_same_blob_across_audit_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stable.md").write_text("S" * 400, encoding="utf-8")
            (root / "changed.py").write_text("C" * 200, encoding="utf-8")
            (root / "boundary.py").write_text("B" * 100, encoding="utf-8")
            result = simulator.simulate_fable_audit_cost(
                root=root,
                stable_paths=["stable.md"],
                changed_paths=["changed.py", "stable.md"],
                cross_boundary_paths=["boundary.py", "changed.py"],
                repository_paths=["stable.md", "changed.py", "boundary.py"],
                expected_output_tokens=20,
                cache_hit_ratio=0.5,
                cache_write_ratio=0.0,
                batch_discount=0.0,
                price_card=eco.PriceCard(10.0, 1.0, 12.5, 50.0),
                hard_budget_usd=10.0,
                chars_per_token=4.0,
            )
            self.assertEqual(result["stable_cacheable_bytes"], 400)
            self.assertEqual(result["changed_unique_bytes"], 200)
            self.assertEqual(result["cross_boundary_unique_bytes"], 100)
            self.assertEqual(result["unique_relevant_bytes"], 700)
            self.assertEqual(result["repository_unique_bytes"], 700)
            self.assertGreater(result["manifest_duplication_ratio"], 0.0)
            self.assertTrue(result["within_budget"])
            self.assertTrue(result["shadow_only"])
            self.assertFalse(result["production_authority"])

    def test_cold_cache_write_cost_is_visible_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stable.md").write_text("S" * 4000, encoding="utf-8")
            result = simulator.simulate_fable_audit_cost(
                root=root,
                stable_paths=["stable.md"],
                changed_paths=[],
                cross_boundary_paths=[],
                repository_paths=["stable.md"],
                expected_output_tokens=0,
                cache_hit_ratio=0.0,
                cache_write_ratio=1.0,
                batch_discount=0.0,
                price_card=eco.PriceCard(10.0, 1.0, 12.5, 50.0),
                hard_budget_usd=0.001,
                chars_per_token=4.0,
            )
            self.assertEqual(result["token_plan"]["stable_tokens"], 1000)
            self.assertEqual(result["cost"]["cache_write_tokens"], 1000)
            self.assertFalse(result["within_budget"])

    def test_path_escape_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "under root"):
                simulator.simulate_fable_audit_cost(
                    root=Path(directory),
                    stable_paths=["../secret"],
                    changed_paths=[],
                    cross_boundary_paths=[],
                    repository_paths=[],
                    expected_output_tokens=0,
                    cache_hit_ratio=0.0,
                    cache_write_ratio=0.0,
                    batch_discount=0.0,
                    price_card=eco.PriceCard(1.0, 1.0, 1.0, 1.0),
                    hard_budget_usd=1.0,
                )


if __name__ == "__main__":
    unittest.main()
