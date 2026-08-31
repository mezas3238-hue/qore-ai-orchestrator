from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import review_sequence_shadow as sequence


class ReviewSequenceShadowTests(unittest.TestCase):
    def plan(self):
        return eco.default_review_plan("QORE-CAND-x", eco.RiskTier.T2)

    def observation(self, stage: str, **overrides):
        values = {
            "completed_stage": stage,
            "verdict": "HALLAZGOS: NINGUNO / VALIDACIÓN OK",
            "run_completed": True,
            "run_success": True,
            "exact_candidate_unchanged": True,
            "evidence_complete": True,
            "anomaly_present": False,
            "finding_present": False,
            "validation_blocked": False,
        }
        values.update(overrides)
        return sequence.ReviewStageObservation(**values)

    def test_clean_expert_advances_to_coder_without_intermediate_sol(self):
        result = sequence.decide_review_sequence_shadow(
            plan=self.plan(), observation=self.observation("DEEPSEEK_EXPERT")
        )
        self.assertEqual(result.action, sequence.ReviewSequenceAction.ADVANCE_PREAUTHORIZED)
        self.assertEqual(result.next_stage, "DEEPSEEK_CODER")
        self.assertTrue(result.shadow_only)

    def test_clean_coder_advances_to_claude(self):
        result = sequence.decide_review_sequence_shadow(
            plan=self.plan(), observation=self.observation("DEEPSEEK_CODER", verdict="CLEAN")
        )
        self.assertEqual(result.next_stage, "CLAUDE")

    def test_clean_claude_still_requires_final_sol(self):
        result = sequence.decide_review_sequence_shadow(
            plan=self.plan(), observation=self.observation("CLAUDE", verdict="CLEAN")
        )
        self.assertEqual(result.action, sequence.ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL)
        self.assertEqual(result.next_stage, "SOL_FINAL")

    def test_finding_blocked_or_anomaly_never_auto_advances(self):
        finding = sequence.decide_review_sequence_shadow(
            plan=self.plan(),
            observation=self.observation("DEEPSEEK_EXPERT", finding_present=True),
        )
        blocked = sequence.decide_review_sequence_shadow(
            plan=self.plan(),
            observation=self.observation("DEEPSEEK_EXPERT", validation_blocked=True),
        )
        anomaly = sequence.decide_review_sequence_shadow(
            plan=self.plan(),
            observation=self.observation("DEEPSEEK_EXPERT", anomaly_present=True),
        )
        self.assertEqual(finding.action, sequence.ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED)
        self.assertEqual(blocked.action, sequence.ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED)
        self.assertEqual(anomaly.action, sequence.ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED)

    def test_candidate_change_invalidates_review(self):
        result = sequence.decide_review_sequence_shadow(
            plan=self.plan(),
            observation=self.observation("DEEPSEEK_EXPERT", exact_candidate_unchanged=False),
        )
        self.assertEqual(result.action, sequence.ReviewSequenceAction.EVIDENCE_REQUIRED)
        self.assertIn("obsolete", result.reason)

    def test_in_progress_is_wait_not_pass(self):
        result = sequence.decide_review_sequence_shadow(
            plan=self.plan(),
            observation=self.observation("DEEPSEEK_EXPERT", run_completed=False),
        )
        self.assertEqual(result.action, sequence.ReviewSequenceAction.WAIT)


if __name__ == "__main__":
    unittest.main()
