from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from economic_control_plane import (
    CandidateIdentity,
    RiskAssessment,
    _require_nonempty,
    _require_sha40,
    sha256_json,
)


@dataclass(frozen=True, slots=True)
class WorkUnitIdentity:
    repository: str
    source_main_sha: str
    source_tree_sha: str
    contract_id: str
    production_authority: bool = False

    def __post_init__(self) -> None:
        _require_nonempty("repository", self.repository)
        _require_sha40("source_main_sha", self.source_main_sha)
        _require_sha40("source_tree_sha", self.source_tree_sha)
        _require_nonempty("contract_id", self.contract_id)
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")

    @property
    def work_unit_id(self) -> str:
        return "QORE-WORK-" + sha256_json(asdict(self))[:24]


class SolSubjectKind(str, Enum):
    WORK_UNIT = "WORK_UNIT"
    CANDIDATE = "CANDIDATE"


def _packet(prefix: str, body: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(body)
    digest = sha256_json(value)
    value["packet_sha256"] = digest
    value["packet_id"] = f"{prefix}-{digest[:24]}"
    return value


def build_sol_decision_packet_v2(
    *,
    subject_kind: SolSubjectKind,
    work_unit: WorkUnitIdentity | None,
    candidate: CandidateIdentity | None,
    risk: RiskAssessment,
    workflow_state: str,
    last_event: str,
    decision_required: str,
    active_contract: Mapping[str, Any],
    semantic_questions: Sequence[str],
    changed_files: Sequence[str],
    diff_summary: Mapping[str, Any],
    findings: Mapping[str, Sequence[Mapping[str, Any]]],
    qg_summary: Mapping[str, Any],
    review_summary: Mapping[str, Any],
    source_slices: Sequence[Mapping[str, Any]],
    budget_remaining_usd: float,
    allowed_transitions: Sequence[str],
) -> dict[str, Any]:
    _require_nonempty("workflow_state", workflow_state)
    _require_nonempty("last_event", last_event)
    _require_nonempty("decision_required", decision_required)
    if budget_remaining_usd < 0:
        raise ValueError("budget_remaining_usd must be non-negative")

    if subject_kind is SolSubjectKind.WORK_UNIT:
        if work_unit is None or candidate is not None:
            raise ValueError("WORK_UNIT subject requires work_unit only")
        subject_id = work_unit.work_unit_id
        subject = asdict(work_unit)
    elif subject_kind is SolSubjectKind.CANDIDATE:
        if candidate is None or work_unit is not None:
            raise ValueError("CANDIDATE subject requires candidate only")
        subject_id = candidate.candidate_id
        subject = asdict(candidate)
    else:
        raise ValueError("unsupported Sol subject kind")

    return _packet(
        "QORE-SOL-PKT2",
        {
            "schema_version": "qore.sol.decision.packet.v2",
            "subject_kind": subject_kind.value,
            "subject_id": subject_id,
            "subject": subject,
            "risk_tier": int(risk.tier),
            "risk_reasons": list(risk.reasons),
            "workflow_state": workflow_state,
            "last_event": last_event,
            "decision_required": decision_required,
            "active_contract": dict(active_contract),
            "open_semantic_questions": list(semantic_questions),
            "changed_files": list(changed_files),
            "diff_summary": dict(diff_summary),
            "findings": {key: list(value) for key, value in findings.items()},
            "qg_summary": dict(qg_summary),
            "review_summary": dict(review_summary),
            "source_slices": list(source_slices),
            "budget_remaining_usd": budget_remaining_usd,
            "allowed_transitions": list(allowed_transitions),
            "production_authority": False,
        },
    )


def build_codex_task_capsule_v2(
    *,
    work_unit: WorkUnitIdentity,
    reference_sha: str | None,
    prior_candidate: CandidateIdentity | None,
    changed_file_allowlist: Sequence[str],
    forbidden_files: Sequence[str],
    contract: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    acceptance_tests: Sequence[str],
    source_slices: Sequence[Mapping[str, Any]],
    relevant_tests: Sequence[str],
    historical_delta: Mapping[str, Any],
) -> dict[str, Any]:
    if reference_sha is not None:
        _require_sha40("reference_sha", reference_sha)
    if prior_candidate is not None:
        if prior_candidate.repository != work_unit.repository:
            raise ValueError("prior candidate repository must match work unit")
        if prior_candidate.base_sha != work_unit.source_main_sha:
            raise ValueError("prior candidate BASE must match work-unit source main")

    return _packet(
        "QORE-CODEX-CAPS2",
        {
            "schema_version": "qore.codex.task.capsule.v2",
            "work_unit_id": work_unit.work_unit_id,
            "work_unit": asdict(work_unit),
            "reference_sha": reference_sha,
            "reference_materialized": reference_sha is not None,
            "prior_candidate": asdict(prior_candidate) if prior_candidate is not None else None,
            "changed_file_allowlist": list(changed_file_allowlist),
            "forbidden_files": list(forbidden_files),
            "contract": dict(contract),
            "findings": list(findings),
            "acceptance_tests": list(acceptance_tests),
            "source_slices": list(source_slices),
            "relevant_tests": list(relevant_tests),
            "historical_delta": dict(historical_delta),
            "missing_evidence_protocol": "NEED_EVIDENCE(symbol/file/test)",
            "worker_instruction": (
                "Do not rediscover repository, source SHA, reference SHA, allowlist, or supplied evidence."
            ),
            "production_authority": False,
        },
    )
