from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import normalize_ai_usage_artifact as usage


class NormalizeAIUsageArtifactTests(unittest.TestCase):
    CANDIDATE = "QORE-CAND-test"

    def test_sol_real_shape(self):
        event = usage.normalize_usage(
            kind="SOL",
            payload={
                "model": "gpt-5.6-sol",
                "input_tokens": 52352,
                "cached_tokens": 0,
                "cache_write_tokens": 22895,
                "output_tokens": 4425,
            },
            session_id="S1",
            candidate_id=self.CANDIDATE,
            stage="ARCHITECT",
        )
        self.assertEqual(event.input_tokens, 52352)
        self.assertEqual(event.cache_write_tokens, 22895)
        self.assertEqual(event.output_tokens, 4425)

    def test_codex_real_shape(self):
        event = usage.normalize_usage(
            kind="CODEX",
            payload={
                "model": "gpt-5.3-codex",
                "input_tokens": 361449,
                "cached_tokens": 312704,
                "cache_write_tokens": 0,
                "output_tokens": 5829,
                "budget_tokens": 126648,
                "turns": 16,
            },
            session_id="S1",
            candidate_id=self.CANDIDATE,
            stage="ENGINEER",
        )
        self.assertEqual(event.cached_input_tokens, 312704)
        self.assertEqual(event.input_tokens, 361449)

    def test_deepseek_cache_hit_and_miss_shape(self):
        event = usage.normalize_usage(
            kind="DEEPSEEK",
            payload={
                "model": "deepseek-v4-pro",
                "usage": {
                    "prompt_cache_hit_tokens": 1664,
                    "prompt_cache_miss_tokens": 41229,
                    "prompt_tokens": 42893,
                    "completion_tokens": 8742,
                },
                "spent_by_currency": {"USD": 0.01},
            },
            session_id="S1",
            candidate_id=self.CANDIDATE,
            stage="EXPERT",
        )
        self.assertEqual(event.input_tokens, 42893)
        self.assertEqual(event.cached_input_tokens, 1664)
        self.assertEqual(event.output_tokens, 8742)
        self.assertEqual(event.observed_usd, 0.01)

    def test_claude_cache_creation_and_read_shape(self):
        event = usage.normalize_usage(
            kind="CLAUDE",
            payload={
                "model": "claude-sonnet-5",
                "usage": {
                    "inputTokens": 24,
                    "cacheCreationInputTokens": 77022,
                    "cacheReadInputTokens": 688399,
                    "outputTokens": 18912,
                },
                "total_cost_usd": 0.6423048,
            },
            session_id="S1",
            candidate_id=self.CANDIDATE,
            stage="REVIEW",
        )
        self.assertEqual(event.input_tokens, 688423)
        self.assertEqual(event.cached_input_tokens, 688399)
        self.assertEqual(event.cache_write_tokens, 77022)
        self.assertEqual(event.output_tokens, 18912)
        self.assertAlmostEqual(event.observed_usd or 0.0, 0.6423048)

    def test_manifest_is_path_bounded_and_emits_source_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "sol-usage.json"
            artifact.write_text(
                json.dumps(
                    {
                        "model": "gpt-5.6-sol",
                        "input_tokens": 10,
                        "cached_tokens": 5,
                        "cache_write_tokens": 2,
                        "output_tokens": 1,
                    }
                ),
                encoding="utf-8",
            )
            normalized = usage.normalize_manifest(
                {
                    "artifacts": [
                        {
                            "path": "sol-usage.json",
                            "kind": "SOL",
                            "session_id": "S1",
                            "candidate_id": self.CANDIDATE,
                            "stage": "ARCHITECT",
                        }
                    ]
                },
                base_dir=root,
            )
            self.assertEqual(len(normalized["events"]), 1)
            self.assertEqual(len(normalized["sources"][0]["sha256"]), 64)
            self.assertFalse(normalized["production_authority"])
            with self.assertRaisesRegex(ValueError, "inside base_dir"):
                usage.normalize_manifest(
                    {
                        "artifacts": [
                            {
                                "path": "../escape.json",
                                "kind": "SOL",
                                "session_id": "S1",
                                "candidate_id": self.CANDIDATE,
                                "stage": "ARCHITECT",
                            }
                        ]
                    },
                    base_dir=root,
                )


if __name__ == "__main__":
    unittest.main()
