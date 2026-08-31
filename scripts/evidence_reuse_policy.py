from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from economic_control_plane import CandidateIdentity


class EvidenceReuseDecision(str, Enum):
    REUSE_FACT_ONLY = "REUSE_FACT_ONLY"
    RECOMPUTE = "RECOMPUTE"
    INVALIDATE_SEMANTIC_AUTHORITY = "INVALIDATE_SEMANTIC_AUTHORITY"


SEMANTIC_EVIDENCE = frozenset(
    {
        "REVIEW_EXPERT",
        "REVIEW_CODER",
        "REVIEW_CLAUDE",
        "SOL_ADJUDICATION",
        "FABLE_AUDIT",
    }
)
HEAD_BOUND_DETERMINISTIC = frozenset({"QG_EVIDENCE", "DIFF_EVIDENCE", "TARGETED_TEST_EVIDENCE"})
BLOB_FACT_EVIDENCE = frozenset({"SOURCE_SLICE", "STATIC_SOURCE_INDEX"})
MATERIALIZATION_EVIDENCE = "MATERIALIZATION_EVIDENCE"


@dataclass(frozen=True, slots=True)
class EvidenceReuseInput:
    evidence_type: str
    old_candidate: CandidateIdentity
    new_candidate: CandidateIdentity
    old_input_digest: str
    new_input_digest: str
    old_contract_version: str
    new_contract_version: str
    relevant_blob_digest_unchanged: bool
    exact_materialization_inputs_unchanged: bool

    def __post_init__(self) -> None:
        for name in (
            "evidence_type",
            "old_input_digest",
            "new_input_digest",
            "old_contract_version",
            "new_contract_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name in (
            "relevant_blob_digest_unchanged",
            "exact_materialization_inputs_unchanged",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be exact bool")


@dataclass(frozen=True, slots=True)
class EvidenceReuseResult:
    decision: EvidenceReuseDecision
    reason: str
    semantic_authority_reused: bool = False
    production_authority: bool = False

    def __post_init__(self) -> None:
        if type(self.semantic_authority_reused) is not bool or self.semantic_authority_reused:
            raise ValueError("semantic authority can never be reused by this policy")
        if type(self.production_authority) is not bool or self.production_authority:
            raise ValueError("production_authority must remain false")


def decide_evidence_reuse(value: EvidenceReuseInput) -> EvidenceReuseResult:
    same_candidate = value.old_candidate.candidate_id == value.new_candidate.candidate_id
    same_head = value.old_candidate.head_sha == value.new_candidate.head_sha
    same_inputs = value.old_input_digest == value.new_input_digest
    same_contract = value.old_contract_version == value.new_contract_version

    if value.evidence_type in SEMANTIC_EVIDENCE:
        if same_candidate and same_inputs and same_contract:
            return EvidenceReuseResult(
                EvidenceReuseDecision.REUSE_FACT_ONLY,
                "exact semantic review record may be referenced as a historical fact on the same candidate; authority is not transferred",
            )
        return EvidenceReuseResult(
            EvidenceReuseDecision.INVALIDATE_SEMANTIC_AUTHORITY,
            "candidate/input/contract change invalidates prior semantic review under freeze discipline",
        )

    if value.evidence_type in HEAD_BOUND_DETERMINISTIC:
        if same_head and same_inputs:
            return EvidenceReuseResult(
                EvidenceReuseDecision.REUSE_FACT_ONLY,
                "same exact HEAD and deterministic inputs permit factual evidence reuse",
            )
        return EvidenceReuseResult(
            EvidenceReuseDecision.RECOMPUTE,
            "HEAD or deterministic inputs changed; fresh deterministic evidence is required",
        )

    if value.evidence_type in BLOB_FACT_EVIDENCE:
        if value.relevant_blob_digest_unchanged and same_inputs:
            return EvidenceReuseResult(
                EvidenceReuseDecision.REUSE_FACT_ONLY,
                "unchanged exact blob/input permits source fact reuse without semantic authority",
            )
        return EvidenceReuseResult(
            EvidenceReuseDecision.RECOMPUTE,
            "relevant source blob or slicing/index inputs changed",
        )

    if value.evidence_type == MATERIALIZATION_EVIDENCE:
        if value.exact_materialization_inputs_unchanged and same_inputs:
            return EvidenceReuseResult(
                EvidenceReuseDecision.REUSE_FACT_ONLY,
                "exact source/reference materialization inputs are unchanged",
            )
        return EvidenceReuseResult(
            EvidenceReuseDecision.RECOMPUTE,
            "materialization inputs changed",
        )

    return EvidenceReuseResult(
        EvidenceReuseDecision.RECOMPUTE,
        "unknown evidence class fails closed to recomputation",
    )
