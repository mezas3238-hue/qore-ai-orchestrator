from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FableActivationEvidence:
    provider_adapter_verified: bool
    controlled_validation_passed: bool
    cost_preflight_verified: bool
    delta_impact_replay_passed: bool
    full_system_recertification_passed: bool
    findings_compaction_verified: bool
    reviewer_substitution_requested: bool = False
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "provider_adapter_verified",
            "controlled_validation_passed",
            "cost_preflight_verified",
            "delta_impact_replay_passed",
            "full_system_recertification_passed",
            "findings_compaction_verified",
            "reviewer_substitution_requested",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


@dataclass(frozen=True, slots=True)
class FableActivationDecision:
    mode: str
    delta_live: bool
    cross_boundary_live: bool
    full_system_live: bool
    hard_cost_preflight_required: bool
    findings_compaction_required: bool
    reviewer_substitution: bool
    blockers: tuple[str, ...]
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.reviewer_substitution:
            raise ValueError("Fable may not substitute mandatory independent reviewers")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


def decide_fable_activation(evidence: FableActivationEvidence) -> FableActivationDecision:
    blockers: list[str] = []
    for field, blocker in (
        (evidence.provider_adapter_verified, "PROVIDER_ADAPTER_VERIFICATION_REQUIRED"),
        (evidence.controlled_validation_passed, "CONTROLLED_VALIDATION_REQUIRED"),
        (evidence.cost_preflight_verified, "COST_PREFLIGHT_RECERTIFICATION_REQUIRED"),
        (evidence.delta_impact_replay_passed, "DELTA_IMPACT_REPLAY_REQUIRED"),
        (evidence.full_system_recertification_passed, "FULL_SYSTEM_RECERTIFICATION_REQUIRED"),
        (evidence.findings_compaction_verified, "FINDINGS_COMPACTION_RECERTIFICATION_REQUIRED"),
    ):
        if not field:
            blockers.append(blocker)
    if evidence.reviewer_substitution_requested:
        blockers.append("FABLE_REVIEWER_SUBSTITUTION_FORBIDDEN")
    live = not blockers
    return FableActivationDecision(
        mode="LIMITED_LIVE" if live else "SHADOW",
        delta_live=live,
        cross_boundary_live=live,
        full_system_live=live,
        hard_cost_preflight_required=True,
        findings_compaction_required=True,
        reviewer_substitution=False,
        blockers=tuple(blockers),
        production_authority=False,
    )


def from_mapping(value: Mapping[str, Any]) -> FableActivationEvidence:
    return FableActivationEvidence(
        provider_adapter_verified=value.get("provider_adapter_verified", False),
        controlled_validation_passed=value.get("controlled_validation_passed", False),
        cost_preflight_verified=value.get("cost_preflight_verified", False),
        delta_impact_replay_passed=value.get("delta_impact_replay_passed", False),
        full_system_recertification_passed=value.get("full_system_recertification_passed", False),
        findings_compaction_verified=value.get("findings_compaction_verified", False),
        reviewer_substitution_requested=value.get("reviewer_substitution_requested", False),
        production_authority=value.get("production_authority", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent Fable audit activation gate.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise SystemExit("Fable activation evidence must be a JSON object")
    decision = decide_fable_activation(from_mapping(raw))
    output = {
        "schema_version": "qore.fable.activation.policy.v1",
        **asdict(decision),
        "blockers": list(decision.blockers),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
