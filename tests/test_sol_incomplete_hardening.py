from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_sol_architect_v2
import select_sol_reasoning_v2


class SolIncompleteHardeningTests(unittest.TestCase):
    def test_reasoning_output_limits_are_bounded_and_leave_xhigh_headroom(self):
        self.assertEqual(
            select_sol_reasoning_v2.OUTPUT_LIMITS,
            {"medium": 6000, "high": 8000, "xhigh": 16000, "max": 20000},
        )
        self.assertLessEqual(max(select_sol_reasoning_v2.OUTPUT_LIMITS.values()), 20000)

    def test_incomplete_usage_is_sanitized_without_model_output(self):
        payload = {
            "id": "resp_test",
            "model": "gpt-5.6-sol",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_tokens"},
            "error": None,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "SECRET MODEL TEXT"}]}],
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 700, "cache_write_tokens": 12},
                "output_tokens": 16000,
                "output_tokens_details": {"reasoning_tokens": 15800},
                "total_tokens": 17000,
            },
        }
        snapshot = {"metrics": {"architect_context_chars": 1234, "full_snapshot_chars": 5678}}
        record = run_sol_architect_v2.safe_usage_record(payload, snapshot, "xhigh", 16000)
        self.assertEqual(record["response_status"], "incomplete")
        self.assertEqual(record["incomplete_reason"], "max_tokens")
        self.assertEqual(record["reasoning_tokens"], 15800)
        self.assertEqual(record["max_output_tokens"], 16000)
        self.assertNotIn("output", record)
        self.assertNotIn("SECRET MODEL TEXT", repr(record))

    def test_provider_messages_and_unsafe_reason_strings_are_not_persisted(self):
        payload = {
            "status": "incomplete",
            "incomplete_details": {"reason": "unsafe reason with spaces and secret=abc"},
            "error": {"code": "server_error", "message": "sensitive provider detail"},
            "usage": {},
        }
        record = run_sol_architect_v2.safe_usage_record(payload, {"metrics": {}}, "high", 8000)
        self.assertIsNone(record["incomplete_reason"])
        self.assertEqual(record["response_error_code"], "server_error")
        self.assertNotIn("sensitive provider detail", repr(record))

    def test_completed_usage_uses_same_terminal_record_contract(self):
        record = run_sol_architect_v2.safe_usage_record(
            {"id": "resp_ok", "model": "gpt-5.6-sol", "status": "completed", "usage": {}},
            {"metrics": {}},
            "medium",
            6000,
        )
        self.assertEqual(record["response_status"], "completed")
        self.assertIsNone(record["incomplete_reason"])


if __name__ == "__main__":
    unittest.main()
