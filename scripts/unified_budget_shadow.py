from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class UnifiedBudgetAssessment:
    orchestrator_spend_usd: float
    external_reviewer_spend_usd: float
    fable_spend_usd: float
    total_end_to_end_spend_usd: float
    hard_budget_usd: float
    remaining_usd: float
    would_exceed_budget: bool
    mandatory_review_pending: bool
    action: str
    shadow_only: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "orchestrator_spend_usd",
            "external_reviewer_spend_usd",
            "fable_spend_usd",
            "total_end_to_end_spend_usd",
            "hard_budget_usd",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if type(self.would_exceed_budget) is not bool:
            raise ValueError("would_exceed_budget must be exact bool")
        if type(self.mandatory_review_pending) is not bool:
            raise ValueError("mandatory_review_pending must be exact bool")
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("unified budget assessment must remain shadow-only")
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")


def _nonnegative_usd(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{name} must be non-negative numeric USD")
    return float(value)


def _sum_cost_rows(rows: Sequence[Mapping[str, Any]], *, name: str) -> float:
    total = 0.0
    for row in rows:
        total += _nonnegative_usd(row.get("cost_usd", 0.0), name=f"{name}.cost_usd")
    return total


def assess_unified_budget_shadow(
    *,
    orchestrator_receipt: Mapping[str, Any],
    reviewer_costs: Sequence[Mapping[str, Any]],
    fable_costs: Sequence[Mapping[str, Any]],
    hard_budget_usd: float,
    mandatory_review_pending: bool,
) -> UnifiedBudgetAssessment:
    hard_budget = _nonnegative_usd(hard_budget_usd, name="hard_budget_usd")
    if type(mandatory_review_pending) is not bool:
        raise ValueError("mandatory_review_pending must be exact bool")

    orchestrator = _nonnegative_usd(
        orchestrator_receipt.get("estimated_spend_usd", 0.0),
        name="estimated_spend_usd",
    )
    reviewers = _sum_cost_rows(reviewer_costs, name="reviewer")
    fable = _sum_cost_rows(fable_costs, name="fable")
    total = orchestrator + reviewers + fable
    remaining = max(0.0, hard_budget - total)
    exceeds = total > hard_budget

    if exceeds and mandatory_review_pending:
        action = "STOP_AND_ESCALATE_BUDGET_WITHOUT_SKIPPING_MANDATORY_REVIEW"
    elif exceeds:
        action = "BUDGET_STOP"
    elif mandatory_review_pending:
        action = "MANDATORY_REVIEW_REMAINS_REQUIRED"
    else:
        action = "WITHIN_BUDGET"

    return UnifiedBudgetAssessment(
        orchestrator_spend_usd=orchestrator,
        external_reviewer_spend_usd=reviewers,
        fable_spend_usd=fable,
        total_end_to_end_spend_usd=total,
        hard_budget_usd=hard_budget,
        remaining_usd=remaining,
        would_exceed_budget=exceeds,
        mandatory_review_pending=mandatory_review_pending,
        action=action,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute end-to-end QORE AI budget in shadow mode.")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--reviewer-costs", required=True, type=Path)
    parser.add_argument("--fable-costs", required=True, type=Path)
    parser.add_argument("--hard-budget-usd", required=True, type=float)
    parser.add_argument("--mandatory-review-pending", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = assess_unified_budget_shadow(
        orchestrator_receipt=json.loads(args.receipt.read_text(encoding="utf-8")),
        reviewer_costs=json.loads(args.reviewer_costs.read_text(encoding="utf-8")),
        fable_costs=json.loads(args.fable_costs.read_text(encoding="utf-8")),
        hard_budget_usd=args.hard_budget_usd,
        mandatory_review_pending=args.mandatory_review_pending,
    )
    args.output.write_text(
        json.dumps(result.__dict__ if hasattr(result, "__dict__") else {
            "orchestrator_spend_usd": result.orchestrator_spend_usd,
            "external_reviewer_spend_usd": result.external_reviewer_spend_usd,
            "fable_spend_usd": result.fable_spend_usd,
            "total_end_to_end_spend_usd": result.total_end_to_end_spend_usd,
            "hard_budget_usd": result.hard_budget_usd,
            "remaining_usd": result.remaining_usd,
            "would_exceed_budget": result.would_exceed_budget,
            "mandatory_review_pending": result.mandatory_review_pending,
            "action": result.action,
            "shadow_only": result.shadow_only,
            "production_authority": result.production_authority,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
