from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fable_activation_policy as target


class FableActivationPolicyTests(unittest.TestCase):
    def evidence(self, **overrides):
        values = dict(
            provider_adapter_verified=True,
            controlled_validation_passed=True,
            cost_preflight_verified=True,
            delta_impact_replay_passed=True,
            full_system_recertification_passed=True,
            findings_compaction_verified=True,
            reviewer_substitution_requested=False,
            production_authority=False,
        )
        values.update(overrides)
        return target.FableActivationEvidence(**values)

    def test_all_independent_gates_allow_limited_live_audits(self):
        decision = target.decide_fable_activation(self.evidence())
        self.assertEqual(decision.mode, "LIMITED_LIVE")
        self.assertTrue(decision.delta_live)
        self.assertTrue(decision.cross_boundary_live)
        self.assertTrue(decision.full_system_live)
        self.assertTrue(decision.hard_cost_preflight_required)
        self.assertTrue(decision.findings_compaction_required)
        self.assertFalse(decision.reviewer_substitution)

    def test_missing_provider_adapter_stays_shadow(self):
        decision = target.decide_fable_activation(self.evidence(provider_adapter_verified=False))
        self.assertEqual(decision.mode, "SHADOW")
        self.assertIn("PROVIDER_ADAPTER_VERIFICATION_REQUIRED", decision.blockers)

    def test_reviewer_substitution_is_forbidden(self):
        decision = target.decide_fable_activation(self.evidence(reviewer_substitution_requested=True))
        self.assertEqual(decision.mode, "SHADOW")
        self.assertIn("FABLE_REVIEWER_SUBSTITUTION_FORBIDDEN", decision.blockers)
        self.assertFalse(decision.reviewer_substitution)

    def test_production_authority_rejected(self):
        with self.assertRaisesRegex(ValueError, "production_authority"):
            self.evidence(production_authority=True)


if __name__ == "__main__":
    unittest.main()
