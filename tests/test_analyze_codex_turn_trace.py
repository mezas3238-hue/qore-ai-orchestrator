from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_codex_turn_trace as analyzer


class AnalyzeCodexTurnTraceTests(unittest.TestCase):
    def test_navigation_before_patch_is_measured(self):
        result = analyzer.analyze_turn_trace(
            {
                "worker_version": "v4",
                "model": "gpt-5.3-codex",
                "input_tokens": 100,
                "cached_tokens": 60,
                "output_tokens": 10,
                "materialized_reference_sha": "a" * 40,
                "budget_tokens": 90,
                "max_total_tokens": 120000,
                "max_turns": 16,
                "turn_trace": [
                    {
                        "turn": 1,
                        "tool": "reference_diff",
                        "input_tokens": 20,
                        "cached_tokens": 0,
                        "output_tokens": 1,
                    },
                    {
                        "turn": 2,
                        "tool": "read_file",
                        "input_tokens": 30,
                        "cached_tokens": 10,
                        "output_tokens": 1,
                    },
                    {
                        "turn": 3,
                        "tool": "apply_patch",
                        "input_tokens": 40,
                        "cached_tokens": 30,
                        "output_tokens": 5,
                    },
                    {
                        "turn": 4,
                        "tool": "run_quality_gate",
                        "input_tokens": 10,
                        "cached_tokens": 20,
                        "output_tokens": 3,
                    },
                ],
            }
        )
        self.assertEqual(result["first_patch_turn"], 3)
        self.assertEqual(result["first_test_turn"], 4)
        self.assertEqual(result["navigation_turns_before_first_patch"], 2)
        self.assertEqual(result["pre_patch_input_tokens"], 50)
        self.assertEqual(result["pre_patch_cached_tokens"], 10)
        self.assertAlmostEqual(result["pre_patch_input_share"], 0.5)
        self.assertFalse(result["production_authority"])

    def test_no_patch_marks_entire_trace_as_pre_patch_cost(self):
        result = analyzer.analyze_turn_trace(
            {
                "input_tokens": 30,
                "cached_tokens": 0,
                "output_tokens": 2,
                "turn_trace": [
                    {
                        "turn": 1,
                        "tool": "list_files",
                        "input_tokens": 10,
                        "cached_tokens": 0,
                        "output_tokens": 1,
                    },
                    {
                        "turn": 2,
                        "tool": "search_text",
                        "input_tokens": 20,
                        "cached_tokens": 0,
                        "output_tokens": 1,
                    },
                ],
            }
        )
        self.assertIsNone(result["first_patch_turn"])
        self.assertEqual(result["pre_patch_input_tokens"], 30)
        self.assertEqual(result["navigation_turns_before_first_patch"], 2)

    def test_noncontiguous_trace_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "contiguous"):
            analyzer.analyze_turn_trace(
                {
                    "input_tokens": 1,
                    "cached_tokens": 0,
                    "output_tokens": 0,
                    "turn_trace": [
                        {
                            "turn": 2,
                            "tool": "read_file",
                            "input_tokens": 1,
                            "cached_tokens": 0,
                            "output_tokens": 0,
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
