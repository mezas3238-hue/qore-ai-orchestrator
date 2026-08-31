from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from economic_control_plane import CandidateIdentity, RiskTier


@dataclass(frozen=True, slots=True)
class BatchLeaf:
    candidate: CandidateIdentity
    risk_tier: RiskTier
    freeze_digest: str
    semantic_change: bool

    def __post_init__(self) -> None:
        if not self.freeze_digest:
            raise ValueError("freeze_digest must be non-empty")
        if type(self.semantic_change) is not bool:
            raise ValueError("semantic_change must be exact bool")


@dataclass(frozen=True, slots=True)
class BatchEligibility:
    eligible: bool
    reasons: tuple[str, ...]
    independent_leaf_ids: tuple[str, ...]
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be exact bool")
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("batch policy must remain shadow-only until recertified")


def evaluate_batch_shadow(leaves: Sequence[BatchLeaf]) -> BatchEligibility:
    if len(leaves) < 2:
        return BatchEligibility(False, ("batch_requires_multiple_independent_leaves",), tuple())

    ids = tuple(leaf.candidate.candidate_id for leaf in leaves)
    if len(set(ids)) != len(ids):
        return BatchEligibility(False, ("duplicate_candidate_identity",), ids)

    freeze_keys = tuple(
        (leaf.candidate.candidate_id, leaf.freeze_digest) for leaf in leaves
    )
    if len(set(freeze_keys)) != len(freeze_keys):
        return BatchEligibility(False, ("duplicate_leaf_freeze",), ids)

    if any(leaf.semantic_change for leaf in leaves):
        return BatchEligibility(False, ("semantic_change_not_batchable",), ids)

    if any(leaf.risk_tier not in {RiskTier.T0, RiskTier.T1} for leaf in leaves):
        return BatchEligibility(False, ("only_tier0_tier1_are_batch_eligible",), ids)

    repositories = {leaf.candidate.repository for leaf in leaves}
    if len(repositories) != 1:
        return BatchEligibility(False, ("cross_repository_batching_disabled",), ids)

    return BatchEligibility(
        True,
        ("independent_nonsemantic_tier0_tier1_leaves",),
        ids,
    )


def invalidate_leaf_after_candidate_change(
    leaves: Sequence[BatchLeaf],
    *,
    changed_candidate_id: str,
) -> tuple[str, ...]:
    """Return only the leaf IDs invalidated by a change; siblings remain independent."""
    matches = tuple(
        leaf.candidate.candidate_id
        for leaf in leaves
        if leaf.candidate.candidate_id == changed_candidate_id
    )
    if len(matches) > 1:
        raise ValueError("candidate identity is not unique inside batch")
    return matches
