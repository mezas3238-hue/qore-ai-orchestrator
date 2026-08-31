from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import replay_economic_policy as replay


class ReplayEconomicPolicyTests(unittest.TestCase):
    def snapshot(self):
        return {
            "candidate": {
                "repository": "mezas3238-hue/qore-core",
                "base_sha": "a" * 40,
                "head_sha": "b" * 40,
                "tree_sha": "c" * 40,
                "synthetic_sha": "d" * 40,
                "production_authority": False,
            },
            "changed_files": ["src/qore/contracts/example.py"],
            "semantic_change": True,
            "contexts": {},
            "cost_events": [],
            "price_cards": {},
            "fable": {
                "milestone_freeze": False,
                "release_recertification": False,
                "security_or_governance_change": False,
                "cross_boundary_change": False,
                "token_plan": {
                    "stable_tokens": 100,
                    "changed_tokens": 10,
                    "cross_boundary_tokens": 0,
                    "expected_output_tokens": 5,
                    "cache_hit_ratio": 0.0,
                    "batch_discount": 0.0,
                },
                "price_card": {
                    "input_per_million": 1.0,
                    "cached_input_per_million": 0.1,
                    "cache_write_per_million": 0.0,
                    "output_per_million": 1.0,
                },
                "hard_budget_usd": 1.0,
            },
        }

    def test_matching_historical_case_passes(self):
        result = replay.replay_corpus(
            [
                {
                    "case_id": "CASE-1",
                    "snapshot": self.snapshot(),
                    "expected": {
                        "risk_tier": 2,
                        "review_stages": [
                            "QG",
                            "DEEPSEEK_EXPERT",
                            "DEEPSEEK_CODER",
                            "CLAUDE",
                            "SOL_FINAL",
                        ],
                        "fable_mode": "DELTA",
                        "production_authority": False,
                    },
                }
            ]
        )
        self.assertTrue(result["all_passed"])
        self.assertEqual(result["failed_case_ids"], [])
        self.assertFalse(result["production_authority"])

    def test_policy_drift_is_visible_not_silently_accepted(self):
        result = replay.replay_corpus(
            [
                {
                    "case_id": "CASE-DRIFT",
                    "snapshot": self.snapshot(),
                    "expected": {"risk_tier": 1},
                }
            ]
        )
        self.assertFalse(result["all_passed"])
        self.assertEqual(result["failed_case_ids"], ["CASE-DRIFT"])


if __name__ == "__main__":
    unittest.main()
