from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from economic_control_plane import (
    AuditTokenPlan,
    CandidateIdentity,
    CostEvent,
    CostLedger,
    PriceCard,
    classify_risk_shadow,
    context_duplication_ratio,
    default_review_plan,
)
from fable_audit_control import preflight_fable_cost_shadow, select_fable_audit_shadow


def _candidate(value: Mapping[str, Any]) -> CandidateIdentity:
    return CandidateIdentity(
        repository=value["repository"],
        base_sha=value["base_sha"],
        head_sha=value["head_sha"],
        tree_sha=value["tree_sha"],
        synthetic_sha=value["synthetic_sha"],
        production_authority=value.get("production_authority", False),
    )


def _cost_event(value: Mapping[str, Any]) -> CostEvent:
    return CostEvent(
        session_id=value["session_id"],
        actor=value["actor"],
        model=value["model"],
        stage=value["stage"],
        candidate_id=value["candidate_id"],
        input_tokens=value.get("input_tokens", 0),
        cached_input_tokens=value.get("cached_input_tokens", 0),
        cache_write_tokens=value.get("cache_write_tokens", 0),
        output_tokens=value.get("output_tokens", 0),
        observed_usd=value.get("observed_usd"),
    )


def _price_card(value: Mapping[str, Any]) -> PriceCard:
    return PriceCard(
        input_per_million=value["input_per_million"],
        cached_input_per_million=value["cached_input_per_million"],
        cache_write_per_million=value.get("cache_write_per_million", 0.0),
        output_per_million=value["output_per_million"],
    )


def build_report(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _candidate(snapshot["candidate"])
    risk = classify_risk_shadow(
        snapshot.get("changed_files", []),
        semantic_change=snapshot.get("semantic_change", False),
        release_or_production_sensitive=snapshot.get(
            "release_or_production_sensitive", False
        ),
    )
    review_plan = default_review_plan(candidate.candidate_id, risk.tier)

    ledger = CostLedger(_cost_event(item) for item in snapshot.get("cost_events", []))
    cards = {
        model: _price_card(value)
        for model, value in snapshot.get("price_cards", {}).items()
    }
    estimated_total = ledger.estimated_total_usd(cards) if ledger.events() else 0.0

    contexts = snapshot.get("contexts", {})
    duplication = context_duplication_ratio(
        {str(key): str(value) for key, value in contexts.items()},
        chunk_chars=snapshot.get("context_chunk_chars", 4096),
    )

    fable_input = snapshot.get("fable")
    fable_report: dict[str, Any] | None = None
    if fable_input is not None:
        selection = select_fable_audit_shadow(
            risk_tier=risk.tier,
            milestone_freeze=fable_input.get("milestone_freeze", False),
            release_recertification=fable_input.get("release_recertification", False),
            security_or_governance_change=fable_input.get(
                "security_or_governance_change", False
            ),
            cross_boundary_change=fable_input.get("cross_boundary_change", False),
        )
        token_plan = AuditTokenPlan(**fable_input["token_plan"])
        price_card = _price_card(fable_input["price_card"])
        gate = preflight_fable_cost_shadow(
            token_plan=token_plan,
            price_card=price_card,
            hard_budget_usd=fable_input["hard_budget_usd"],
        )
        fable_report = {
            "mode": selection.mode.value,
            "reasons": list(selection.reasons),
            "shadow_only": selection.shadow_only,
            "cost_gate": {
                **asdict(gate.estimate),
                "hard_budget_usd": gate.hard_budget_usd,
                "within_budget": gate.within_budget,
                "shadow_only": gate.shadow_only,
            },
        }

    return {
        "schema_version": "qore.economic.shadow.report.v1",
        "candidate_id": candidate.candidate_id,
        "head_sha": candidate.head_sha,
        "risk": {
            "tier": int(risk.tier),
            "reasons": list(risk.reasons),
            "shadow_only": risk.shadow_only,
        },
        "review_plan": {
            "stages": list(review_plan.stages),
            "final_sol_required": review_plan.final_sol_required,
            "policy_status": "NO_REVIEWER_REDUCTION_ACTIVATED",
        },
        "cost": {
            "event_count": len(ledger.events()),
            "observed_usd": ledger.total_observed_usd(),
            "estimated_total_usd": estimated_total,
        },
        "context": {
            "duplication_ratio": duplication,
            "source_count": len(contexts),
        },
        "fable": fable_report,
        "production_authority": False,
        "shadow_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a zero-model-call QORE economic/routing shadow report."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_report(snapshot)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
