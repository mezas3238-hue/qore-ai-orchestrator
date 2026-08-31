from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ActivationEvidence:
    recertification_passed: bool
    exact_head_ci_success: bool
    compact_packet_replay_match: bool
    codex_capsule_replay_match: bool
    clean_pass_replay_match: bool
    live_validation_completed: bool
    live_validation_quality_match: bool
    live_validation_within_budget: bool
    reviewer_suppression_recertified: bool = False
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "recertification_passed",
            "exact_head_ci_success",
            "compact_packet_replay_match",
            "codex_capsule_replay_match",
            "clean_pass_replay_match",
            "live_validation_completed",
            "live_validation_quality_match",
            "live_validation_within_budget",
            "reviewer_suppression_recertified",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


@dataclass(frozen=True, slots=True)
class ActivationPolicy:
    mode: str
    compact_sol_context_live: bool
    codex_task_capsule_live: bool
    clean_pass_auto_advance_live: bool
    fable_incremental_audit_live: bool
    batch_review_live: bool
    reviewer_suppression_live: bool
    mandatory_review_chain: tuple[str, ...]
    final_sol_required: bool
    blockers: tuple[str, ...]
    production_authority: bool = False

    def __post_init__(self) -> None:
        for name in (
            "compact_sol_context_live",
            "codex_task_capsule_live",
            "clean_pass_auto_advance_live",
            "fable_incremental_audit_live",
            "batch_review_live",
            "reviewer_suppression_live",
            "final_sol_required",
            "production_authority",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")
        if self.production_authority:
            raise ValueError("production_authority must remain false")
        if self.reviewer_suppression_live:
            raise ValueError("reviewer suppression is not permitted by current activation policy")
        if not self.final_sol_required:
            raise ValueError("final Sol adjudication remains mandatory")


def decide_activation(evidence: ActivationEvidence) -> ActivationPolicy:
    blockers: list[str] = []
    if not evidence.exact_head_ci_success:
        blockers.append("EXACT_HEAD_CI_REQUIRED")
    if not evidence.recertification_passed:
        blockers.append("ECONOMIC_RECERTIFICATION_REQUIRED")
    if not evidence.compact_packet_replay_match:
        blockers.append("COMPACT_PACKET_REPLAY_REQUIRED")
    if not evidence.codex_capsule_replay_match:
        blockers.append("CODEX_CAPSULE_REPLAY_REQUIRED")
    if not evidence.clean_pass_replay_match:
        blockers.append("CLEAN_PASS_REPLAY_REQUIRED")
    if not evidence.live_validation_completed:
        blockers.append("CONTROLLED_LIVE_VALIDATION_REQUIRED")
    if evidence.live_validation_completed and not evidence.live_validation_quality_match:
        blockers.append("LIVE_QUALITY_NONREGRESSION_FAILED")
    if evidence.live_validation_completed and not evidence.live_validation_within_budget:
        blockers.append("LIVE_BUDGET_GATE_FAILED")
    if evidence.reviewer_suppression_recertified:
        # Separate future policy version is required even if data someday supports it.
        blockers.append("REVIEWER_SUPPRESSION_REQUIRES_NEW_POLICY_VERSION")

    live = not blockers
    return ActivationPolicy(
        mode="LIMITED_LIVE" if live else "SHADOW",
        compact_sol_context_live=live,
        codex_task_capsule_live=live,
        clean_pass_auto_advance_live=live,
        fable_incremental_audit_live=False,
        batch_review_live=False,
        reviewer_suppression_live=False,
        mandatory_review_chain=(
            "QG",
            "DEEPSEEK_EXPERT",
            "DEEPSEEK_CODER",
            "CLAUDE",
            "SOL_FINAL",
        ),
        final_sol_required=True,
        blockers=tuple(blockers),
        production_authority=False,
    )


def evidence_from_mapping(value: Mapping[str, Any]) -> ActivationEvidence:
    return ActivationEvidence(
        recertification_passed=value.get("recertification_passed", False),
        exact_head_ci_success=value.get("exact_head_ci_success", False),
        compact_packet_replay_match=value.get("compact_packet_replay_match", False),
        codex_capsule_replay_match=value.get("codex_capsule_replay_match", False),
        clean_pass_replay_match=value.get("clean_pass_replay_match", False),
        live_validation_completed=value.get("live_validation_completed", False),
        live_validation_quality_match=value.get("live_validation_quality_match", False),
        live_validation_within_budget=value.get("live_validation_within_budget", False),
        reviewer_suppression_recertified=value.get("reviewer_suppression_recertified", False),
        production_authority=value.get("production_authority", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed live optimization activation policy.")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    raw = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise SystemExit("activation evidence must be a JSON object")
    policy = decide_activation(evidence_from_mapping(raw))
    output = {
        "schema_version": "qore.optimization.activation.policy.v1",
        **asdict(policy),
        "mandatory_review_chain": list(policy.mandatory_review_chain),
        "blockers": list(policy.blockers),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.require_live and policy.mode != "LIMITED_LIVE":
        raise SystemExit(41)


if __name__ == "__main__":
    main()
