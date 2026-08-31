from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_economic_shadow_report as shadow


class EconomicShadowReportTests(unittest.TestCase):
    def test_report_is_zero_authority_shadow_and_keeps_reviewer_chain(self):
        candidate = {
            "repository": "mezas3238-hue/qore-core",
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "tree_sha": "c" * 40,
            "synthetic_sha": "d" * 40,
            "production_authority": False,
        }
        candidate_id = shadow._candidate(candidate).candidate_id
        report = shadow.build_report(
            {
                "candidate": candidate,
                "changed_files": ["src/qore/contracts/example.py"],
                "semantic_change": True,
                "contexts": {"first": "abcdabcd", "second": "abcdwxyz"},
                "context_chunk_chars": 4,
                "cost_events": [
                    {
                        "session_id": "S1",
                        "actor": "SOL",
                        "model": "gpt-5.6",
                        "stage": "ARCHITECT",
                        "candidate_id": candidate_id,
                        "input_tokens": 100_000,
                        "cached_input_tokens": 80_000,
                        "cache_write_tokens": 0,
                        "output_tokens": 5_000,
                        "observed_usd": 0.4,
                    }
                ],
                "price_cards": {},
                "fable": {
                    "milestone_freeze": False,
                    "release_recertification": False,
                    "security_or_governance_change": False,
                    "cross_boundary_change": False,
                    "token_plan": {
                        "stable_tokens": 500_000,
                        "changed_tokens": 50_000,
                        "cross_boundary_tokens": 25_000,
                        "expected_output_tokens": 20_000,
                        "cache_hit_ratio": 0.8,
                        "batch_discount": 0.0,
                    },
                    "price_card": {
                        "input_per_million": 10.0,
                        "cached_input_per_million": 1.0,
                        "cache_write_per_million": 0.0,
                        "output_per_million": 50.0,
                    },
                    "hard_budget_usd": 10.0,
                },
            }
        )
        self.assertTrue(report["shadow_only"])
        self.assertFalse(report["production_authority"])
        self.assertEqual(report["risk"]["tier"], 2)
        self.assertEqual(report["fable"]["mode"], "DELTA")
        self.assertEqual(
            report["review_plan"]["stages"],
            ["QG", "DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE", "SOL_FINAL"],
        )
        self.assertEqual(
            report["review_plan"]["policy_status"],
            "NO_REVIEWER_REDUCTION_ACTIVATED",
        )
        self.assertAlmostEqual(report["cost"]["estimated_total_usd"], 0.4)
        self.assertAlmostEqual(report["context"]["duplication_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
