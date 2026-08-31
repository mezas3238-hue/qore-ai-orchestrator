from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import economic_control_plane as eco
import semantic_delta_gate_shadow as gate


class SemanticDeltaGateShadowTests(unittest.TestCase):
    def base(self, **overrides):
        values = {
            "risk_tier": eco.RiskTier.T1,
            "candidate_binding_complete": True,
            "deterministic_checks_complete": True,
            "deterministic_failure_present": False,
            "unresolved_semantic_questions": (),
            "engineering_judgment_required": False,
            "reviewer_contradiction_present": False,
            "human_authority_required": False,
            "production_authority": False,
        }
        values.update(overrides)
        return gate.SemanticDeltaInput(**values)

    def test_missing_tree_or_other_binding_requires_evidence_not_ai(self):
        result = gate.evaluate_semantic_delta_shadow(
            self.base(candidate_binding_complete=False)
        )
        self.assertEqual(result.decision, gate.SemanticGateDecision.EVIDENCE_REQUIRED)
        self.assertIsNone(result.model_role_hint)

    def test_mechanical_deterministic_failure_is_not_sent_to_ai(self):
        result = gate.evaluate_semantic_delta_shadow(
            self.base(deterministic_failure_present=True)
        )
        self.assertEqual(
            result.decision, gate.SemanticGateDecision.DETERMINISTIC_CONTINUE
        )
        self.assertIn("before_ai", result.reasons[0])

    def test_semantic_question_routes_to_sol_and_engineering_only_to_codex(self):
        semantic = gate.evaluate_semantic_delta_shadow(
            self.base(unresolved_semantic_questions=("Does this preserve identity?",))
        )
        engineering = gate.evaluate_semantic_delta_shadow(
            self.base(engineering_judgment_required=True)
        )
        self.assertEqual(semantic.decision, gate.SemanticGateDecision.AI_JUDGMENT_REQUIRED)
        self.assertEqual(semantic.model_role_hint, "SOL")
        self.assertEqual(engineering.model_role_hint, "CODEX")

    def test_high_assurance_tier_does_not_silently_skip_semantic_gate(self):
        result = gate.evaluate_semantic_delta_shadow(
            self.base(risk_tier=eco.RiskTier.T3)
        )
        self.assertEqual(result.decision, gate.SemanticGateDecision.AI_JUDGMENT_REQUIRED)
        self.assertEqual(result.model_role_hint, "SOL")

    def test_human_authority_preempts_ai(self):
        result = gate.evaluate_semantic_delta_shadow(
            self.base(human_authority_required=True)
        )
        self.assertEqual(
            result.decision, gate.SemanticGateDecision.HUMAN_AUTHORITY_REQUIRED
        )
        self.assertIsNone(result.model_role_hint)

    def test_production_authority_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "production_authority"):
            self.base(production_authority=True)


if __name__ == "__main__":
    unittest.main()
