from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import optimization_activation_policy as target


class OptimizationActivationPolicyTests(unittest.TestCase):
    def evidence(self, **overrides):
        values = dict(
            recertification_passed=True,
            exact_head_ci_success=True,
            compact_packet_replay_match=True,
            codex_capsule_replay_match=True,
            clean_pass_replay_match=True,
            live_validation_completed=True,
            live_validation_quality_match=True,
            live_validation_within_budget=True,
            reviewer_suppression_recertified=False,
            production_authority=False,
        )
        values.update(overrides)
        return target.ActivationEvidence(**values)

    def test_complete_evidence_allows_limited_live_without_reviewer_reduction(self):
        policy = target.decide_activation(self.evidence())
        self.assertEqual(policy.mode, "LIMITED_LIVE")
        self.assertTrue(policy.compact_sol_context_live)
        self.assertTrue(policy.codex_task_capsule_live)
        self.assertTrue(policy.clean_pass_auto_advance_live)
        self.assertFalse(policy.reviewer_suppression_live)
        self.assertFalse(policy.batch_review_live)
        self.assertTrue(policy.final_sol_required)

    def test_missing_live_validation_stays_shadow(self):
        policy = target.decide_activation(self.evidence(live_validation_completed=False))
        self.assertEqual(policy.mode, "SHADOW")
        self.assertIn("CONTROLLED_LIVE_VALIDATION_REQUIRED", policy.blockers)
        self.assertFalse(policy.clean_pass_auto_advance_live)

    def test_reviewer_suppression_requires_new_policy_version(self):
        policy = target.decide_activation(self.evidence(reviewer_suppression_recertified=True))
        self.assertEqual(policy.mode, "SHADOW")
        self.assertIn("REVIEWER_SUPPRESSION_REQUIRES_NEW_POLICY_VERSION", policy.blockers)
        self.assertFalse(policy.reviewer_suppression_live)

    def test_production_authority_rejected(self):
        with self.assertRaisesRegex(ValueError, "production_authority"):
            self.evidence(production_authority=True)


if __name__ == "__main__":
    unittest.main()
