from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import trigger_reviewer_recovery as trigger  # noqa: E402
import trigger_reviewer_recovery_retry as retry  # noqa: E402


class ReviewerRecoveryRetryTests(unittest.TestCase):
    def test_no_prior_attempt_is_eligible(self) -> None:
        with patch.object(retry, "matching_recoveries", return_value=[]):
            eligible, matches = retry.retry_eligible("token", 123)
        self.assertTrue(eligible)
        self.assertEqual(matches, [])

    def test_one_or_two_terminal_failures_without_side_effects_can_retry(self) -> None:
        for runs in (
            [{"id": 10, "status": "completed", "conclusion": "failure"}],
            [
                {"id": 10, "status": "completed", "conclusion": "failure"},
                {"id": 11, "status": "completed", "conclusion": "failure"},
            ],
        ):
            with self.subTest(count=len(runs)):
                with (
                    patch.object(retry, "matching_recoveries", return_value=runs),
                    patch.object(retry, "attempt_has_side_effect_evidence", return_value=False),
                ):
                    eligible, _ = retry.retry_eligible("token", 123)
                self.assertTrue(eligible)

    def test_active_success_or_side_effect_attempt_blocks_retry(self) -> None:
        active = {"id": 10, "status": "in_progress", "conclusion": None}
        with patch.object(retry, "matching_recoveries", return_value=[active]):
            eligible, _ = retry.retry_eligible("token", 123)
        self.assertFalse(eligible)

        success = {"id": 10, "status": "completed", "conclusion": "success"}
        with patch.object(retry, "matching_recoveries", return_value=[success]):
            eligible, _ = retry.retry_eligible("token", 123)
        self.assertFalse(eligible)

        failed = {"id": 10, "status": "completed", "conclusion": "failure"}
        with (
            patch.object(retry, "matching_recoveries", return_value=[failed]),
            patch.object(retry, "attempt_has_side_effect_evidence", return_value=True),
        ):
            eligible, _ = retry.retry_eligible("token", 123)
        self.assertFalse(eligible)

    def test_third_terminal_failure_exhausts_retry_cap(self) -> None:
        runs = [
            {"id": 10, "status": "completed", "conclusion": "failure"},
            {"id": 11, "status": "completed", "conclusion": "failure"},
            {"id": 12, "status": "completed", "conclusion": "failure"},
        ]
        with (
            patch.object(retry, "matching_recoveries", return_value=runs),
            patch.object(retry, "attempt_has_side_effect_evidence", return_value=False),
        ):
            with self.assertRaisesRegex(trigger.RecoveryTriggerError, "retry cap exhausted"):
                retry.retry_eligible("token", 123)

    def test_unexpected_terminal_conclusion_fails_closed(self) -> None:
        run = {"id": 10, "status": "completed", "conclusion": "neutral"}
        with patch.object(retry, "matching_recoveries", return_value=[run]):
            with self.assertRaisesRegex(trigger.RecoveryTriggerError, "not safely retryable"):
                retry.retry_eligible("token", 123)


if __name__ == "__main__":
    unittest.main()
