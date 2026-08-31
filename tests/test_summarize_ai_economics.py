from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import summarize_ai_economics as economics


class SummarizeAIEconomicsTests(unittest.TestCase):
    def test_groups_calls_and_cache_ratio(self):
        result = economics.summarize(
            events=[
                {
                    "session_id": "S1",
                    "actor": "SOL",
                    "model": "gpt-5.6-sol",
                    "stage": "ARCHITECT",
                    "candidate_id": "C1",
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "cache_write_tokens": 20,
                    "output_tokens": 10,
                    "observed_usd": 0.5,
                },
                {
                    "session_id": "S1",
                    "actor": "SOL",
                    "model": "gpt-5.6-sol",
                    "stage": "ARCHITECT",
                    "candidate_id": "C1",
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "cache_write_tokens": 0,
                    "output_tokens": 5,
                    "observed_usd": 0.25,
                },
            ],
            price_cards={},
        )
        self.assertEqual(result["totals"]["calls"], 2)
        self.assertEqual(result["totals"]["input_tokens"], 200)
        self.assertEqual(result["totals"]["cached_input_tokens"], 100)
        self.assertAlmostEqual(result["totals"]["cache_read_ratio"], 0.5)
        self.assertAlmostEqual(result["totals"]["estimated_or_observed_usd"], 0.75)
        self.assertEqual(len(result["by_actor_model_stage"]), 1)
        self.assertFalse(result["production_authority"])

    def test_missing_price_card_fails_for_unobserved_cost(self):
        with self.assertRaises(KeyError):
            economics.summarize(
                events=[
                    {
                        "session_id": "S1",
                        "actor": "CODEX",
                        "model": "gpt-5.3-codex",
                        "stage": "ENGINEER",
                        "candidate_id": "C1",
                        "input_tokens": 100,
                        "cached_input_tokens": 0,
                        "cache_write_tokens": 0,
                        "output_tokens": 1,
                    }
                ],
                price_cards={},
            )


if __name__ == "__main__":
    unittest.main()
