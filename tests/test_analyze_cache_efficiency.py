from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_cache_efficiency as cache


class AnalyzeCacheEfficiencyTests(unittest.TestCase):
    def test_detects_cache_writes_without_read_hits(self):
        result = cache.analyze_cache_efficiency(
            [
                {
                    "session_id": "S1",
                    "actor": "SOL",
                    "model": "gpt-5.6-sol",
                    "input_tokens": 50_000,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 20_000,
                },
                {
                    "session_id": "S1",
                    "actor": "SOL",
                    "model": "gpt-5.6-sol",
                    "input_tokens": 50_000,
                    "cached_input_tokens": 0,
                    "cache_write_tokens": 20_000,
                },
            ]
        )
        row = result["sessions"][0]
        self.assertTrue(row["cache_write_without_any_read_hit"])
        self.assertEqual(row["cache_write_tokens"], 40_000)
        self.assertIsNone(row["write_to_hit_ratio"])
        self.assertIn("DOES_NOT_JUSTIFY_DUPLICATED_CONTEXT", result["audit_rule"])
        self.assertFalse(result["production_authority"])

    def test_aggregates_cache_read_ratio(self):
        result = cache.analyze_cache_efficiency(
            [
                {
                    "session_id": "S2",
                    "actor": "CODEX",
                    "model": "gpt-5.3-codex",
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "cache_write_tokens": 0,
                },
                {
                    "session_id": "S2",
                    "actor": "CODEX",
                    "model": "gpt-5.3-codex",
                    "input_tokens": 100,
                    "cached_input_tokens": 60,
                    "cache_write_tokens": 0,
                },
            ]
        )
        self.assertAlmostEqual(result["sessions"][0]["cache_hit_ratio"], 0.7)


if __name__ == "__main__":
    unittest.main()
