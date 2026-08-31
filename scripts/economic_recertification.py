from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class RecertificationEvidence:
    replay_cases: int
    replay_mismatches: int
    shadow_completion_observations: int
    shadow_decision_mismatches: int
    material_findings_baseline: int
    material_findings_preserved: int
    post_merge_escape_defects: int
    baseline_usd: float
    optimized_projected_usd: float
    reviewer_suppression_enabled: bool = False
    final_sol_required: bool = True
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "replay_cases",
            "replay_mismatches",
            "shadow_completion_observations",
            "shadow_decision_mismatches",
            "material_findings_baseline",
            "material_findings_preserved",
            "post_merge_escape_defects",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative exact int")
        if self.baseline_usd < 0 or self.optimized_projected_usd < 0:
            raise ValueError("cost values must be non-negative")
        for name in (
            "reviewer_suppression_enabled",
            "final_sol_required",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


@dataclass(frozen=True, slots=True)
class RecertificationDecision:
    passed: bool
    blockers: tuple[str, ...]
    savings_usd: float
    savings_ratio: float
    reviewer_policy: str
    production_authority: bool = False

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise ValueError("passed must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


def recertify(
    evidence: RecertificationEvidence,
    *,
    minimum_replay_cases: int = 1,
    minimum_shadow_observations: int = 0,
    minimum_savings_ratio: float = 0.0,
) -> RecertificationDecision:
    if type(minimum_replay_cases) is not int or minimum_replay_cases < 1:
        raise ValueError("minimum_replay_cases must be a positive exact int")
    if type(minimum_shadow_observations) is not int or minimum_shadow_observations < 0:
        raise ValueError("minimum_shadow_observations must be a non-negative exact int")
    if not 0.0 <= minimum_savings_ratio < 1.0:
        raise ValueError("minimum_savings_ratio must be in [0, 1)")

    blockers: list[str] = []
    if evidence.replay_cases < minimum_replay_cases:
        blockers.append("INSUFFICIENT_REPLAY_CASES")
    if evidence.replay_mismatches:
        blockers.append("REPLAY_DECISION_MISMATCH")
    if evidence.shadow_completion_observations < minimum_shadow_observations:
        blockers.append("INSUFFICIENT_SHADOW_COMPLETION_OBSERVATIONS")
    if evidence.shadow_decision_mismatches:
        blockers.append("SHADOW_DECISION_MISMATCH")
    if evidence.material_findings_preserved < evidence.material_findings_baseline:
        blockers.append("MATERIAL_FINDING_DETECTION_REGRESSION")
    if evidence.post_merge_escape_defects:
        blockers.append("POST_MERGE_DEFECT_ESCAPE")
    if evidence.reviewer_suppression_enabled:
        blockers.append("REVIEWER_SUPPRESSION_NOT_RECERTIFIED")
    if not evidence.final_sol_required:
        blockers.append("FINAL_SOL_AUTHORITY_REMOVED")

    if evidence.baseline_usd > 0:
        savings_usd = evidence.baseline_usd - evidence.optimized_projected_usd
        savings_ratio = savings_usd / evidence.baseline_usd
        if evidence.optimized_projected_usd >= evidence.baseline_usd:
            blockers.append("NO_ECONOMIC_IMPROVEMENT")
        elif savings_ratio < minimum_savings_ratio:
            blockers.append("SAVINGS_BELOW_REQUIRED_THRESHOLD")
    else:
        savings_usd = 0.0
        savings_ratio = 0.0
        blockers.append("BASELINE_COST_EVIDENCE_REQUIRED")

    return RecertificationDecision(
        passed=not blockers,
        blockers=tuple(blockers),
        savings_usd=savings_usd,
        savings_ratio=savings_ratio,
        reviewer_policy="QG->DEEPSEEK_EXPERT->DEEPSEEK_CODER->CLAUDE->SOL_FINAL",
        production_authority=False,
    )


def evidence_from_mapping(value: Mapping[str, Any]) -> RecertificationEvidence:
    return RecertificationEvidence(
        replay_cases=value.get("replay_cases", 0),
        replay_mismatches=value.get("replay_mismatches", 0),
        shadow_completion_observations=value.get("shadow_completion_observations", 0),
        shadow_decision_mismatches=value.get("shadow_decision_mismatches", 0),
        material_findings_baseline=value.get("material_findings_baseline", 0),
        material_findings_preserved=value.get("material_findings_preserved", 0),
        post_merge_escape_defects=value.get("post_merge_escape_defects", 0),
        baseline_usd=float(value.get("baseline_usd", 0.0)),
        optimized_projected_usd=float(value.get("optimized_projected_usd", 0.0)),
        reviewer_suppression_enabled=value.get("reviewer_suppression_enabled", False),
        final_sol_required=value.get("final_sol_required", True),
        production_authority=value.get("production_authority", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Economic + quality non-regression recertification gate.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--minimum-replay-cases", type=int, default=1)
    parser.add_argument("--minimum-shadow-observations", type=int, default=0)
    parser.add_argument("--minimum-savings-ratio", type=float, default=0.0)
    parser.add_argument("--require-pass", action="store_true")
    args = parser.parse_args()
    raw = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise SystemExit("recertification evidence must be a JSON object")
    decision = recertify(
        evidence_from_mapping(raw),
        minimum_replay_cases=args.minimum_replay_cases,
        minimum_shadow_observations=args.minimum_shadow_observations,
        minimum_savings_ratio=args.minimum_savings_ratio,
    )
    output = {
        "schema_version": "qore.economic.recertification.v1",
        **asdict(decision),
        "blockers": list(decision.blockers),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_pass and not decision.passed:
        raise SystemExit(40)


if __name__ == "__main__":
    main()
