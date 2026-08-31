from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from economic_control_plane import RiskTier


class SemanticGateDecision(str, Enum):
    DETERMINISTIC_CONTINUE = "DETERMINISTIC_CONTINUE"
    AI_JUDGMENT_REQUIRED = "AI_JUDGMENT_REQUIRED"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    HUMAN_AUTHORITY_REQUIRED = "HUMAN_AUTHORITY_REQUIRED"


@dataclass(frozen=True, slots=True)
class SemanticDeltaInput:
    risk_tier: RiskTier
    candidate_binding_complete: bool
    deterministic_checks_complete: bool
    deterministic_failure_present: bool
    unresolved_semantic_questions: tuple[str, ...]
    engineering_judgment_required: bool
    reviewer_contradiction_present: bool
    human_authority_required: bool
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "candidate_binding_complete",
            "deterministic_checks_complete",
            "deterministic_failure_present",
            "engineering_judgment_required",
            "reviewer_contradiction_present",
            "human_authority_required",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")
        for question in self.unresolved_semantic_questions:
            if not isinstance(question, str) or not question.strip():
                raise ValueError("semantic questions must be non-empty strings")


@dataclass(frozen=True, slots=True)
class SemanticGateResult:
    decision: SemanticGateDecision
    reasons: tuple[str, ...]
    model_role_hint: str | None
    shadow_only: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("semantic delta gate must remain shadow-only")
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")


def evaluate_semantic_delta_shadow(value: SemanticDeltaInput) -> SemanticGateResult:
    if value.human_authority_required:
        return SemanticGateResult(
            SemanticGateDecision.HUMAN_AUTHORITY_REQUIRED,
            ("explicit_human_authority_required",),
            None,
        )

    if not value.candidate_binding_complete:
        return SemanticGateResult(
            SemanticGateDecision.EVIDENCE_REQUIRED,
            ("exact_candidate_binding_incomplete",),
            None,
        )

    if not value.deterministic_checks_complete:
        return SemanticGateResult(
            SemanticGateDecision.EVIDENCE_REQUIRED,
            ("deterministic_checks_incomplete",),
            None,
        )

    if value.deterministic_failure_present:
        return SemanticGateResult(
            SemanticGateDecision.DETERMINISTIC_CONTINUE,
            ("deterministic_failure_must_be_resolved_before_ai",),
            None,
        )

    reasons: list[str] = []
    if value.unresolved_semantic_questions:
        reasons.append("unresolved_semantic_questions")
    if value.engineering_judgment_required:
        reasons.append("engineering_judgment_required")
    if value.reviewer_contradiction_present:
        reasons.append("reviewer_contradiction_present")

    if reasons:
        if value.reviewer_contradiction_present or value.unresolved_semantic_questions:
            role = "SOL"
        else:
            role = "CODEX"
        return SemanticGateResult(
            SemanticGateDecision.AI_JUDGMENT_REQUIRED,
            tuple(reasons),
            role,
        )

    if value.risk_tier in {RiskTier.T3, RiskTier.T4}:
        return SemanticGateResult(
            SemanticGateDecision.AI_JUDGMENT_REQUIRED,
            ("high_assurance_risk_tier_requires_semantic_gate",),
            "SOL",
        )

    return SemanticGateResult(
        SemanticGateDecision.DETERMINISTIC_CONTINUE,
        ("no_new_semantic_uncertainty_detected",),
        None,
    )


def semantic_questions(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if isinstance(value, str) and value.strip())
