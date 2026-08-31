from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import replay_economic_policy as target


class EconomicPolicyReplayFixtureTests(unittest.TestCase):
    def test_t0_t4_fixture_replays_without_mismatch(self):
        cases = json.loads((ROOT / "fixtures/economic_policy_replay.json").read_text(encoding="utf-8"))
        result = target.replay_corpus(cases)
        self.assertEqual(result["case_count"], 5)
        self.assertEqual(result["passed_count"], 5)
        self.assertTrue(result["all_passed"])
        self.assertEqual(result["failed_case_ids"], [])
        self.assertFalse(result["production_authority"])


if __name__ == "__main__":
    unittest.main()
