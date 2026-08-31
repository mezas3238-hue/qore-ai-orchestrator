from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from economic_control_plane import (
    AuditCostEstimate,
    AuditTokenPlan,
    CandidateIdentity,
    PriceCard,
    RiskTier,
    estimate_fable_audit_cost,
    sha256_json,
)


class FableAuditMode(str, Enum):
    NONE = "NONE"
    DELTA = "DELTA"
    CROSS_BOUNDARY = "CROSS_BOUNDARY"
    FULL_SYSTEM = "FULL_SYSTEM"


@dataclass(frozen=True, slots=True)
class FableAuditSelection:
    mode: FableAuditMode
    reasons: tuple[str, ...]
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("Fable policy must remain shadow-only until economic recertification")


def select_fable_audit_shadow(
    *,
    risk_tier: RiskTier,
    milestone_freeze: bool,
    release_recertification: bool,
    security_or_governance_change: bool,
    cross_boundary_change: bool,
) -> FableAuditSelection:
    for name, value in (
        ("milestone_freeze", milestone_freeze),
        ("release_recertification", release_recertification),
        ("security_or_governance_change", security_or_governance_change),
        ("cross_boundary_change", cross_boundary_change),
    ):
        if type(value) is not bool:
            raise ValueError(f"{name} must be exact bool")

    if release_recertification or milestone_freeze or risk_tier is RiskTier.T4:
        reasons = []
        if release_recertification:
            reasons.append("release_recertification")
        if milestone_freeze:
            reasons.append("milestone_freeze")
        if risk_tier is RiskTier.T4:
            reasons.append("tier4")
        return FableAuditSelection(FableAuditMode.FULL_SYSTEM, tuple(reasons))

    if security_or_governance_change or cross_boundary_change or risk_tier is RiskTier.T3:
        reasons = []
        if security_or_governance_change:
            reasons.append("security_or_governance_change")
        if cross_boundary_change:
            reasons.append("cross_boundary_change")
        if risk_tier is RiskTier.T3:
            reasons.append("tier3")
        return FableAuditSelection(FableAuditMode.CROSS_BOUNDARY, tuple(reasons))

    if risk_tier is RiskTier.T2:
        return FableAuditSelection(FableAuditMode.DELTA, ("tier2_semantic_change",))

    return FableAuditSelection(FableAuditMode.NONE, ("ordinary_tier0_or_tier1_change",))


@dataclass(frozen=True, slots=True)
class FableCostGate:
    estimate: AuditCostEstimate
    hard_budget_usd: float
    within_budget: bool
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if self.hard_budget_usd < 0:
            raise ValueError("hard_budget_usd must be non-negative")
        if type(self.within_budget) is not bool:
            raise ValueError("within_budget must be exact bool")
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("Fable cost gate must remain shadow-only until recertified")


def preflight_fable_cost_shadow(
    *,
    token_plan: AuditTokenPlan,
    price_card: PriceCard,
    hard_budget_usd: float,
) -> FableCostGate:
    estimate = estimate_fable_audit_cost(token_plan, price_card)
    return FableCostGate(
        estimate=estimate,
        hard_budget_usd=hard_budget_usd,
        within_budget=estimate.estimated_usd <= hard_budget_usd,
    )


def build_fable_audit_package(
    *,
    selection: FableAuditSelection,
    system_freeze: Mapping[str, Mapping[str, str]],
    primary_candidate: CandidateIdentity,
    changed_since_last_audit: Sequence[Mapping[str, Any]],
    dependency_graph: Mapping[str, Any],
    authority_graph: Mapping[str, Any],
    trust_boundaries: Sequence[Mapping[str, Any]],
    data_flows: Sequence[Mapping[str, Any]],
    ai_orchestration_graph: Mapping[str, Any],
    contracts: Sequence[Mapping[str, Any]],
    invariants: Sequence[str],
    forbidden_transitions: Sequence[str],
    qg_evidence: Sequence[Mapping[str, Any]],
    known_attack_surfaces: Sequence[str],
    source_index: Sequence[Mapping[str, Any]],
    symbol_index: Sequence[Mapping[str, Any]],
    cross_component_interfaces: Sequence[Mapping[str, Any]],
    prior_audit_evidence_refs: Sequence[str],
    hard_budget_usd: float,
) -> dict[str, Any]:
    if selection.mode is FableAuditMode.NONE:
        raise ValueError("cannot build a Fable package when audit mode is NONE")
    if hard_budget_usd < 0:
        raise ValueError("hard_budget_usd must be non-negative")

    body: dict[str, Any] = {
        "schema_version": "qore.fable.audit.package.v1",
        "audit_mode": selection.mode.value,
        "audit_reasons": list(selection.reasons),
        "system_freeze": {key: dict(value) for key, value in sorted(system_freeze.items())},
        "primary_candidate_id": primary_candidate.candidate_id,
        "primary_candidate": asdict(primary_candidate),
        "changed_since_last_audit": list(changed_since_last_audit),
        "dependency_graph": dict(dependency_graph),
        "authority_graph": dict(authority_graph),
        "trust_boundaries": list(trust_boundaries),
        "data_flows": list(data_flows),
        "ai_orchestration_graph": dict(ai_orchestration_graph),
        "contracts": list(contracts),
        "invariants": list(invariants),
        "forbidden_transitions": list(forbidden_transitions),
        "qg_evidence": list(qg_evidence),
        "known_attack_surfaces": list(known_attack_surfaces),
        "source_index": list(source_index),
        "symbol_index": list(symbol_index),
        "cross_component_interfaces": list(cross_component_interfaces),
        "prior_audit_evidence_refs": list(prior_audit_evidence_refs),
        "hard_budget_usd": hard_budget_usd,
        "instructions": (
            "Do not trust prior conclusions. Attempt to falsify the stated guarantees and "
            "return only independently reproducible material findings or an explicit clean verdict."
        ),
        "output_contract": [
            "FINDING_ID",
            "SEVERITY",
            "AFFECTED_COMPONENT",
            "EXACT_FILE_OR_SYMBOL",
            "VIOLATED_INVARIANT",
            "REPRODUCIBLE_WITNESS",
            "ATTACK_OR_FAILURE_PATH",
            "EXPECTED",
            "ACTUAL",
            "SMALLEST_SAFE_FIX",
            "CONFIDENCE",
            "EVIDENCE_REFERENCES",
        ],
        "production_authority": False,
        "shadow_only": True,
    }
    digest = sha256_json(body)
    body["package_sha256"] = digest
    body["package_id"] = f"QORE-FABLE-AUDIT-{digest[:24]}"
    return body


def compact_fable_findings(
    findings: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Deterministic finding compaction before one high-value Sol adjudication."""
    groups: dict[str, list[Mapping[str, Any]]] = {
        "REPRODUCED": [],
        "DISPROVED": [],
        "DUPLICATE": [],
        "SEMANTIC_DISPUTE": [],
        "UNVERIFIED": [],
    }
    for finding in findings:
        status = finding.get("deterministic_status", "UNVERIFIED")
        if status not in groups:
            status = "UNVERIFIED"
        groups[status].append(finding)
    for key in groups:
        groups[key] = sorted(
            groups[key],
            key=lambda item: (
                str(item.get("finding_id", "")),
                sha256_json(dict(item)),
            ),
        )
    return groups
