from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import analyze_model_context_shadow as analysis


class AnalyzeModelContextShadowTests(unittest.TestCase):
    def test_stable_prefix_and_largest_sections_are_measured(self):
        context = {
            "schema_version": "qore.model.context.v1",
            "main_sha": "a" * 40,
            "full_snapshot_sha256": "b" * 64,
            "stable_context": {
                "readme": "R" * 500,
                "constitution_documents": [{"content": "C" * 800}],
            },
            "dynamic_context": {
                "source_main_sha": "a" * 40,
                "focused_pull_requests": [{"body": "P" * 300}],
            },
            "engineer_context": {
                "source_main_sha": "a" * 40,
                "focused_pull_requests": [{"body": "P" * 300}],
            },
        }
        result = analysis.analyze_model_context(context)
        self.assertGreater(result["stable_context_chars"], 1000)
        self.assertGreater(result["stable_share_of_architect_context"], 0.5)
        self.assertTrue(result["shadow_prompt_cache_key"].startswith("qore-sol-stable-v2-"))
        self.assertEqual(result["largest_sections"][0]["scope"], "stable")
        self.assertIn("DO_NOT_REMOVE_SEMANTIC_EVIDENCE", result["policy"])
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["production_authority"])

    def test_wrong_schema_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unexpected"):
            analysis.analyze_model_context({"schema_version": "wrong"})


if __name__ == "__main__":
    unittest.main()
