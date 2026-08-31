from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from economic_control_plane import PreauthorizedReviewPlan


class ReviewSequenceAction(str, Enum):
    ADVANCE_PREAUTHORIZED = "ADVANCE_PREAUTHORIZED"
    SOL_ADJUDICATION_REQUIRED = "SOL_ADJUDICATION_REQUIRED"
    EVIDENCE_REQUIRED = "EVIDENCE_REQUIRED"
    WAIT = "WAIT"
    COMPLETE_FOR_FINAL_SOL = "COMPLETE_FOR_FINAL_SOL"


CLEAN_VERDICTS = frozenset({"HALLAZGOS: NINGUNO / VALIDACIÓN OK", "CLEAN"})


@dataclass(frozen=True, slots=True)
class ReviewStageObservation:
    completed_stage: str
    verdict: str | None
    run_completed: bool
    run_success: bool
    exact_candidate_unchanged: bool
    evidence_complete: bool
    anomaly_present: bool
    finding_present: bool
    validation_blocked: bool

    def __post_init__(self) -> None:
        if not self.completed_stage:
            raise ValueError("completed_stage must be non-empty")
        for name in (
            "run_completed",
            "run_success",
            "exact_candidate_unchanged",
            "evidence_complete",
            "anomaly_present",
            "finding_present",
            "validation_blocked",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")


@dataclass(frozen=True, slots=True)
class ReviewSequenceDecision:
    action: ReviewSequenceAction
    next_stage: str | None
    reason: str
    shadow_only: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("review sequence must remain shadow-only")
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")


def decide_review_sequence_shadow(
    *,
    plan: PreauthorizedReviewPlan,
    observation: ReviewStageObservation,
) -> ReviewSequenceDecision:
    if observation.completed_stage not in plan.stages:
        raise ValueError("completed stage is not in preauthorized plan")

    if not observation.run_completed:
        return ReviewSequenceDecision(
            ReviewSequenceAction.WAIT,
            None,
            "exact reviewer job has not completed",
        )

    if not observation.exact_candidate_unchanged:
        return ReviewSequenceDecision(
            ReviewSequenceAction.EVIDENCE_REQUIRED,
            None,
            "candidate changed; prior review is obsolete",
        )

    if not observation.run_success or not observation.evidence_complete:
        return ReviewSequenceDecision(
            ReviewSequenceAction.EVIDENCE_REQUIRED,
            None,
            "review run/evidence is incomplete or unsuccessful",
        )

    if observation.validation_blocked or observation.anomaly_present:
        return ReviewSequenceDecision(
            ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED,
            "SOL_FINAL" if observation.completed_stage == "CLAUDE" else None,
            "blocked validation or anomaly requires semantic adjudication",
        )

    verdict = (observation.verdict or "").strip()
    if observation.finding_present or verdict not in CLEAN_VERDICTS:
        return ReviewSequenceDecision(
            ReviewSequenceAction.SOL_ADJUDICATION_REQUIRED,
            None,
            "material finding or non-clean verdict requires adjudication",
        )

    index = plan.stages.index(observation.completed_stage)
    if observation.completed_stage == "CLAUDE":
        if plan.final_sol_required:
            return ReviewSequenceDecision(
                ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL,
                "SOL_FINAL",
                "all independent reviewer stages are clean; final Sol adjudication remains mandatory",
            )
        raise ValueError("final Sol adjudication cannot be disabled in current policy")

    if index + 1 >= len(plan.stages):
        return ReviewSequenceDecision(
            ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL,
            "SOL_FINAL" if plan.final_sol_required else None,
            "preauthorized review stages completed",
        )

    next_stage = plan.stages[index + 1]
    if next_stage == "SOL_FINAL":
        return ReviewSequenceDecision(
            ReviewSequenceAction.COMPLETE_FOR_FINAL_SOL,
            "SOL_FINAL",
            "next preauthorized stage is mandatory final Sol adjudication",
        )

    return ReviewSequenceDecision(
        ReviewSequenceAction.ADVANCE_PREAUTHORIZED,
        next_stage,
        "explicit clean verdict on unchanged exact candidate permits deterministic stage advancement",
    )
