from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HistoricalCostBaselineTests(unittest.TestCase):
    def test_observed_session_totals_reconcile(self):
        value = json.loads((ROOT / "fixtures/historical_session_33344862110.json").read_text(encoding="utf-8"))
        self.assertEqual(value["schema_version"], "qore.historical.ai.cost.baseline.v1")
        self.assertEqual(value["sol"]["calls"], 3)
        self.assertEqual(value["codex"]["jobs"], 3)
        self.assertEqual(value["codex"]["turns"], 48)
        self.assertAlmostEqual(
            value["sol"]["cost_usd"] + value["codex"]["cost_usd"],
            1.6322345,
            places=7,
        )
        self.assertAlmostEqual(value["observed_orchestrator_spend_usd"], 1.632235, places=6)
        self.assertFalse(value["reviewer_spend_included"])
        self.assertFalse(value["production_authority"])

    def test_waste_signature_is_frozen(self):
        value = json.loads((ROOT / "fixtures/historical_session_33344862110.json").read_text(encoding="utf-8"))
        third = value["codex"]["job_summaries"][2]
        self.assertEqual(third["first_patch_turn"], 16)
        self.assertEqual(third["changed_files"], 3)
        self.assertEqual(third["materialized_reference_sha"], "df934e5585f59dd0aef17f9ece108d6f39204470")
        self.assertEqual(value["stop_reason"], "CODEX_JOB_CAP_REACHED")


if __name__ == "__main__":
    unittest.main()
