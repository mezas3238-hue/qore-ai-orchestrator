from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from economic_control_plane import (
    ModelCallIntent,
    RiskTier,
    RouteAction,
    classify_risk_shadow,
    default_review_plan,
    schedule_model_call_shadow,
)
from fable_audit_control import select_fable_audit_shadow
from review_sequence_shadow import (
    ReviewSequenceAction,
    ReviewStageObservation,
    decide_review_sequence_shadow,
)
from semantic_delta_gate_shadow import (
    SemanticDeltaInput,
    SemanticGateDecision,
    evaluate_semantic_delta_shadow,
    semantic_questions,
)


class ControllerAction(str, Enum):
    DETERMINISTIC_WORK = "DETERMINISTIC_WORK"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    CALL_MODEL = "CALL_MODEL"
    WAIT = "WAIT"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
    BUDGET_STOP = "BUDGET_STOP"
    ADVANCE_PREAUTHORIZED_REVIEW = "ADVANCE_PREAUTHORIZED_REVIEW"
    SOL_ADJUDICATION_REQUIRED = "SOL_ADJUDICATION_REQUIRED"
    FINAL_SOL_REQUIRED = "FINAL_SOL_REQUIRED"


@dataclass(frozen=True, slots=True)
class ControllerInput:
    changed_files: tuple[str, ...]
    semantic_change: bool
    release_or_production_sensitive: bool
    candidate_binding_complete: bool
    deterministic_checks_complete: bool
    deterministic_failure_present: bool
    unresolved_semantic_questions: tuple[str, ...]
    engineering_judgment_required: bool
    reviewer_contradiction_present: bool
    human_authority_required: bool
    external_agent_pending: bool
    model_call_estimated_tokens: int
    model_call_estimated_usd: float
    remaining_budget_usd: float
    candidate_or_work_unit_id: str
    required_evidence: tuple[str, ...]
    expected_information_gain: str
    invalidation_rule: str
    milestone_freeze: bool = False
    fable_release_recertification: bool = False
    security_or_governance_change: bool = False
    cross_boundary_change: bool = False
    review_observation: ReviewStageObservation | None = None
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "semantic_change",
            "release_or_production_sensitive",
            "candidate_binding_complete",
            "deterministic_checks_complete",
            "deterministic_failure_present",
            "engineering_judgment_required",
            "reviewer_contradiction_present",
            "human_authority_required",
            "external_agent_pending",
            "milestone_freeze",
            "fable_release_recertification",
            "security_or_governance_change",
            "cross_boundary_change",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")
        if type(self.model_call_estimated_tokens) is not int or self.model_call_estimated_tokens < 0:
            raise ValueError("model_call_estimated_tokens must be a non-negative exact int")
        if self.model_call_estimated_usd < 0 or self.remaining_budget_usd < 0:
            raise ValueError("budget values must be non-negative")
        if not self.candidate_or_work_unit_id:
            raise ValueError("candidate_or_work_unit_id must be non-empty")


@dataclass(frozen=True, slots=True)
class ControllerDecision:
    action: ControllerAction
    model_role: str | None
    next_stage: str | None
    reasons: tuple[str, ...]
    risk_tier: int
    risk_reasons: tuple[str, ...]
    fable_mode: str
    fable_reasons: tuple[str, ...]
    shadow_only: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("controller must remain shadow-only until recertified")
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")


def decide_controller_shadow(value: ControllerInput) -> ControllerDecision:
    risk = classify_risk_shadow(
        value.changed_files,
        semantic_change=value.semantic_change,
        release_or_production_sensitive=value.release_or_production_sensitive,
    )
    fable = select_fable_audit_shadow(
        risk_tier=risk.tier,
        milestone_freeze=value.milestone_freeze,
        release_recertification=value.fable_release_recertification,
        security_or_governance_change=value.security_or_governance_change,
        cross_boundary_change=value.cross_boundary_change,
    )

    if value.review_observation is not None:
        plan = default_review_plan(value.candidate_or_work_unit_id, risk.tier)
        review = decide_review_sequence_shadow(
            plan=plan,
            observation=value.review_observation,
        )
        mapping = {
            ReviewSequenceAction.ADVANCE_PREAUTHORIZED: ControllerAction.ADVANCE_PREAUTHORIZED_REVIEW,
            ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED: ControllerAction.SOL_ADJUDICATION_REQUIRED,
            ReviewSequenceAction.EVIDENCE_REQUIRED: ControllerAction.EVIDENCE_REQUIRED,
            ReviewSequenceAction.WAIT: ControllerAction.WAIT,
            ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL: ControllerAction.FINAL_SOL_REQUIRED,
        }
        return ControllerDecision(
            action=mapping[review.action],
            model_role=("SOL" if review.action in {
                ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED,
                ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL,
            } else None),
            next_stage=review.next_stage,
            reasons=(review.reason,),
            risk_tier=int(risk.tier),
            risk_reasons=risk.reasons,
            fable_mode=fable.mode.value,
            fable_reasons=fable.reasons,
        )

    semantic = evaluate_semantic_delta_shadow(
        SemanticDeltaInput(
            risk_tier=risk.tier,
            candidate_binding_complete=value.candidate_binding_complete,
            deterministic_checks_complete=value.deterministic_checks_complete,
            deterministic_failure_present=value.deterministic_failure_present,
            unresolved_semantic_questions=semantic_questions(
                value.unresolved_semantic_questions
            ),
            engineering_judgment_required=value.engineering_judgment_required,
            reviewer_contradiction_present=value.reviewer_contradiction_present,
            human_authority_required=value.human_authority_required,
            production_authority=False,
        )
    )

    if semantic.decision is SemanticGateDecision.HUMAN_AUTHORITY_REQUIRED:
        return ControllerDecision(
            ControllerAction.HUMAN_DECISION_REQUIRED,
            None,
            None,
            semantic.reasons,
            int(risk.tier),
            risk.reasons,
            fable.mode.value,
            fable.reasons,
        )
    if semantic.decision is SemanticGateDecision.EVIDENCE_REQUIRED:
        return ControllerDecision(
            ControllerAction.EVIDENCE_REQUIRED,
            None,
            None,
            semantic.reasons,
            int(risk.tier),
            risk.reasons,
            fable.mode.value,
            fable.reasons,
        )
    if value.external_agent_pending:
        return ControllerDecision(
            ControllerAction.WAIT,
            None,
            None,
            ("exact_external_agent_pending",),
            int(risk.tier),
            risk.reasons,
            fable.mode.value,
            fable.reasons,
        )
    if semantic.decision is SemanticGateDecision.DETERMINISTIC_CONTINUE:
        return ControllerDecision(
            ControllerAction.DETERMINISTIC_WORK,
            None,
            None,
            semantic.reasons,
            int(risk.tier),
            risk.reasons,
            fable.mode.value,
            fable.reasons,
        )

    role = semantic.model_role_hint
    if role is None:
        raise ValueError("AI judgment required without model role hint")
    if value.model_call_estimated_tokens <= 0:
        return ControllerDecision(
            ControllerAction.EVIDENCE_REQUIRED,
            None,
            None,
            ("model_call_token_estimate_required_before_dispatch",),
            int(risk.tier),
            risk.reasons,
            fable.mode.value,
            fable.reasons,
        )
    intent = ModelCallIntent(
        semantic_uncertainty=";".join(semantic.reasons),
        model_role=role,
        candidate_id=value.candidate_or_work_unit_id,
        required_evidence=value.required_evidence,
        expected_information_gain=value.expected_information_gain,
        estimated_tokens=value.model_call_estimated_tokens,
        estimated_usd=value.model_call_estimated_usd,
        remaining_budget_usd=value.remaining_budget_usd,
        invalidation_rule=value.invalidation_rule,
    )
    scheduled = schedule_model_call_shadow(
        intent=intent,
        deterministic_work_pending=False,
        external_agent_pending=False,
        human_authority_required=False,
    )
    if scheduled.action is RouteAction.BUDGET_STOP:
        action = ControllerAction.BUDGET_STOP
    elif scheduled.action is RouteAction.CALL_MODEL:
        action = ControllerAction.CALL_MODEL
    else:
        raise ValueError(f"unexpected scheduler action after semantic gate: {scheduled.action}")
    return ControllerDecision(
        action,
        role,
        None,
        semantic.reasons,
        int(risk.tier),
        risk.reasons,
        fable.mode.value,
        fable.reasons,
    )


def decision_to_json(decision: ControllerDecision) -> dict[str, Any]:
    return asdict(decision)
