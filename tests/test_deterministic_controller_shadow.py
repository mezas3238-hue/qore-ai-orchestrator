from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import deterministic_controller_shadow as controller
import review_sequence_shadow as review


class DeterministicControllerShadowTests(unittest.TestCase):
    def base(self, **overrides):
        values = {
            "changed_files": ("src/local.py",),
            "semantic_change": False,
            "release_or_production_sensitive": False,
            "candidate_binding_complete": True,
            "deterministic_checks_complete": True,
            "deterministic_failure_present": False,
            "unresolved_semantic_questions": (),
            "engineering_judgment_required": False,
            "reviewer_contradiction_present": False,
            "human_authority_required": False,
            "external_agent_pending": False,
            "model_call_estimated_tokens": 10000,
            "model_call_estimated_usd": 0.25,
            "remaining_budget_usd": 5.0,
            "candidate_or_work_unit_id": "QORE-WORK-x",
            "required_evidence": ("diff",),
            "expected_information_gain": "resolve bounded uncertainty",
            "invalidation_rule": "source HEAD change invalidates decision",
            "milestone_freeze": False,
            "fable_release_recertification": False,
            "security_or_governance_change": False,
            "cross_boundary_change": False,
            "review_observation": None,
            "production_authority": False,
        }
        values.update(overrides)
        return controller.ControllerInput(**values)

    def test_deterministic_first_suppresses_unnecessary_ai(self):
        result = controller.decide_controller_shadow(self.base())
        self.assertEqual(result.action, controller.ControllerAction.DETERMINISTIC_WORK)
        self.assertIsNone(result.model_role)
        self.assertTrue(result.shadow_only)
        self.assertFalse(result.production_authority)

    def test_semantic_question_routes_one_sol_call_when_budget_allows(self):
        result = controller.decide_controller_shadow(
            self.base(
                changed_files=("src/qore/contracts/x.py",),
                semantic_change=True,
                unresolved_semantic_questions=("Does identity remain exact?",),
            )
        )
        self.assertEqual(result.action, controller.ControllerAction.CALL_MODEL)
        self.assertEqual(result.model_role, "SOL")
        self.assertEqual(result.risk_tier, 2)
        self.assertEqual(result.fable_mode, "DELTA")

    def test_over_budget_semantic_call_stops_before_dispatch(self):
        result = controller.decide_controller_shadow(
            self.base(
                semantic_change=True,
                unresolved_semantic_questions=("semantic dispute",),
                model_call_estimated_usd=6.0,
                remaining_budget_usd=5.0,
            )
        )
        self.assertEqual(result.action, controller.ControllerAction.BUDGET_STOP)
        self.assertEqual(result.model_role, "SOL")

    def test_missing_exact_binding_requests_evidence_not_ai(self):
        result = controller.decide_controller_shadow(
            self.base(
                candidate_binding_complete=False,
                semantic_change=True,
                unresolved_semantic_questions=("question",),
            )
        )
        self.assertEqual(result.action, controller.ControllerAction.EVIDENCE_REQUIRED)
        self.assertIsNone(result.model_role)

    def test_pending_external_job_waits(self):
        result = controller.decide_controller_shadow(
            self.base(external_agent_pending=True)
        )
        self.assertEqual(result.action, controller.ControllerAction.WAIT)

    def test_clean_reviewer_advances_without_intermediate_sol(self):
        observation = review.ReviewStageObservation(
            completed_stage="DEEPSEEK_EXPERT",
            verdict="HALLAZGOS: NINGUNO / VALIDACIÓN OK",
            run_completed=True,
            run_success=True,
            exact_candidate_unchanged=True,
            evidence_complete=True,
            anomaly_present=False,
            finding_present=False,
            validation_blocked=False,
        )
        result = controller.decide_controller_shadow(
            self.base(
                changed_files=("src/qore/contracts/x.py",),
                semantic_change=True,
                candidate_or_work_unit_id="QORE-CAND-x",
                review_observation=observation,
            )
        )
        self.assertEqual(
            result.action, controller.ControllerAction.ADVANCE_PREAUTHORIZED_REVIEW
        )
        self.assertEqual(result.next_stage, "DEEPSEEK_CODER")
        self.assertIsNone(result.model_role)

    def test_clean_claude_keeps_final_sol_mandatory(self):
        observation = review.ReviewStageObservation(
            completed_stage="CLAUDE",
            verdict="CLEAN",
            run_completed=True,
            run_success=True,
            exact_candidate_unchanged=True,
            evidence_complete=True,
            anomaly_present=False,
            finding_present=False,
            validation_blocked=False,
        )
        result = controller.decide_controller_shadow(
            self.base(
                changed_files=("src/qore/contracts/x.py",),
                semantic_change=True,
                candidate_or_work_unit_id="QORE-CAND-x",
                review_observation=observation,
            )
        )
        self.assertEqual(result.action, controller.ControllerAction.FINAL_SOL_REQUIRED)
        self.assertEqual(result.model_role, "SOL")
        self.assertEqual(result.next_stage, "SOL_FINAL")

    def test_tier3_security_change_advises_cross_boundary_fable(self):
        result = controller.decide_controller_shadow(
            self.base(
                changed_files=("scripts/security_authority.py",),
                security_or_governance_change=True,
            )
        )
        self.assertEqual(result.risk_tier, 3)
        self.assertEqual(result.fable_mode, "CROSS_BOUNDARY")
        self.assertEqual(result.action, controller.ControllerAction.CALL_MODEL)
        self.assertEqual(result.model_role, "SOL")


if __name__ == "__main__":
    unittest.main()
