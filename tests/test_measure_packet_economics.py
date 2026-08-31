from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure_packet_economics as measure


class MeasurePacketEconomicsTests(unittest.TestCase):
    def test_compact_packet_reduction_is_measured_not_assumed(self):
        baseline = {"context": "x" * 1000, "history": "y" * 1000}
        packet = {"decision": "x" * 100}
        result = measure.measure_packet_economics(
            baseline_context=baseline,
            compact_packet=packet,
            chars_per_token=4.0,
        )
        self.assertGreater(result["char_reduction_ratio"], 0.8)
        self.assertGreater(result["estimated_token_reduction_ratio"], 0.8)
        self.assertEqual(
            result["interpretation"], "MEASUREMENT_ONLY_NOT_LIVE_ROUTING_AUTHORITY"
        )
        self.assertFalse(result["production_authority"])

    def test_invalid_token_assumption_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            measure.estimate_tokens_from_chars(100, chars_per_token=0)


if __name__ == "__main__":
    unittest.main()
