from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SHA40_LEN = 40


class RouteAction(str, Enum):
    CALL_MODEL = "CALL_MODEL"
    NO_CALL_REQUIRED = "NO_CALL_REQUIRED"
    WAIT = "WAIT"
    HUMAN_DECISION_REQUIRED = "HUMAN_DECISION_REQUIRED"
    BUDGET_STOP = "BUDGET_STOP"


class RiskTier(int, Enum):
    T0 = 0
    T1 = 1
    T2 = 2
    T3 = 3
    T4 = 4


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_sha40(name: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != SHA40_LEN:
        raise ValueError(f"{name} must be a 40-character SHA")
    if any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be lowercase hexadecimal")


def _require_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _require_bool(name: str, value: bool) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be exact bool")


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    repository: str
    base_sha: str
    head_sha: str
    tree_sha: str
    synthetic_sha: str
    production_authority: bool = False

    def __post_init__(self) -> None:
        _require_nonempty("repository", self.repository)
        _require_sha40("base_sha", self.base_sha)
        _require_sha40("head_sha", self.head_sha)
        _require_sha40("tree_sha", self.tree_sha)
        _require_sha40("synthetic_sha", self.synthetic_sha)
        _require_bool("production_authority", self.production_authority)
        if self.production_authority:
            raise ValueError("production_authority must remain false")

    @property
    def candidate_id(self) -> str:
        return "QORE-CAND-" + sha256_json(asdict(self))[:24]


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    candidate_id: str
    evidence_type: str
    input_digest: str
    tool_version: str
    command: str
    output_digest: str
    reusable_across_same_head: bool
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty("candidate_id", self.candidate_id)
        _require_nonempty("evidence_type", self.evidence_type)
        _require_nonempty("input_digest", self.input_digest)
        _require_nonempty("tool_version", self.tool_version)
        _require_nonempty("command", self.command)
        _require_nonempty("output_digest", self.output_digest)
        _require_bool("reusable_across_same_head", self.reusable_across_same_head)

    @property
    def evidence_id(self) -> str:
        return "QORE-EVID-" + sha256_json(asdict(self))[:24]


class EvidenceLedger:
    """Append-only immutable evidence index; never grants semantic authority."""

    def __init__(self, records: Iterable[EvidenceRecord] = ()) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        for record in records:
            self.append(record)

    def append(self, record: EvidenceRecord) -> str:
        evidence_id = record.evidence_id
        existing = self._records.get(evidence_id)
        if existing is not None and existing != record:
            raise ValueError("evidence ID collision")
        self._records[evidence_id] = record
        return evidence_id

    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def reusable(
        self,
        *,
        candidate_id: str,
        evidence_type: str,
        input_digest: str,
    ) -> tuple[EvidenceRecord, ...]:
        return tuple(
            record
            for record in self.records()
            if record.candidate_id == candidate_id
            and record.evidence_type == evidence_type
            and record.input_digest == input_digest
            and record.reusable_across_same_head
        )

    def write_jsonl(self, path: Path) -> None:
        payload = "\n".join(canonical_json(asdict(record)) for record in self.records())
        path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class CostEvent:
    session_id: str
    actor: str
    model: str
    stage: str
    candidate_id: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    observed_usd: float | None = None

    def __post_init__(self) -> None:
        for name in ("session_id", "actor", "model", "stage", "candidate_id"):
            _require_nonempty(name, getattr(self, name))
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "output_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative exact int")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.observed_usd is not None and (
            isinstance(self.observed_usd, bool) or self.observed_usd < 0
        ):
            raise ValueError("observed_usd must be non-negative or null")

    @property
    def event_id(self) -> str:
        return "QORE-COST-" + sha256_json(asdict(self))[:24]


@dataclass(frozen=True, slots=True)
class PriceCard:
    input_per_million: float
    cached_input_per_million: float
    cache_write_per_million: float
    output_per_million: float

    def __post_init__(self) -> None:
        for value in (
            self.input_per_million,
            self.cached_input_per_million,
            self.cache_write_per_million,
            self.output_per_million,
        ):
            if isinstance(value, bool) or value < 0:
                raise ValueError("price card values must be non-negative")

    def estimate(self, event: CostEvent) -> float:
        uncached = event.input_tokens - event.cached_input_tokens
        return (
            uncached * self.input_per_million
            + event.cached_input_tokens * self.cached_input_per_million
            + event.cache_write_tokens * self.cache_write_per_million
            + event.output_tokens * self.output_per_million
        ) / 1_000_000


class CostLedger:
    """Unified deterministic ledger for Sol, Codex and independent reviewers."""

    def __init__(self, events: Iterable[CostEvent] = ()) -> None:
        self._events: dict[str, CostEvent] = {}
        for event in events:
            self.append(event)

    def append(self, event: CostEvent) -> str:
        event_id = event.event_id
        existing = self._events.get(event_id)
        if existing is not None and existing != event:
            raise ValueError("cost event ID collision")
        self._events[event_id] = event
        return event_id

    def events(self) -> tuple[CostEvent, ...]:
        return tuple(self._events[key] for key in sorted(self._events))

    def total_observed_usd(self) -> float:
        return sum(event.observed_usd or 0.0 for event in self._events.values())

    def estimated_total_usd(self, price_cards: Mapping[str, PriceCard]) -> float:
        total = 0.0
        for event in self._events.values():
            if event.observed_usd is not None:
                total += event.observed_usd
                continue
            card = price_cards.get(event.model)
            if card is None:
                raise KeyError(f"missing price card for {event.model}")
            total += card.estimate(event)
        return total


def chunk_digests(
    sources: Mapping[str, str],
    *,
    chunk_chars: int = 4096,
) -> tuple[tuple[str, str, int], ...]:
    if type(chunk_chars) is not int or chunk_chars <= 0:
        raise ValueError("chunk_chars must be a positive exact int")
    rows: list[tuple[str, str, int]] = []
    for source in sorted(sources):
        text = sources[source]
        if not isinstance(text, str):
            raise ValueError("source content must be text")
        for offset in range(0, len(text), chunk_chars):
            chunk = text[offset : offset + chunk_chars]
            digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            rows.append((source, digest, len(chunk)))
    return tuple(rows)


def context_duplication_ratio(
    sources: Mapping[str, str],
    *,
    chunk_chars: int = 4096,
) -> float:
    rows = chunk_digests(sources, chunk_chars=chunk_chars)
    total = sum(size for _, _, size in rows)
    if total == 0:
        return 0.0
    unique: dict[str, int] = {}
    for _, digest, size in rows:
        unique.setdefault(digest, size)
    unique_total = sum(unique.values())
    return max(0.0, min(1.0, 1.0 - unique_total / total))


@dataclass(frozen=True, slots=True)
class AuditTokenPlan:
    stable_tokens: int
    changed_tokens: int
    cross_boundary_tokens: int
    expected_output_tokens: int
    cache_hit_ratio: float
    batch_discount: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "stable_tokens",
            "changed_tokens",
            "cross_boundary_tokens",
            "expected_output_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative exact int")
        for name in ("cache_hit_ratio", "batch_discount"):
            value = getattr(self, name)
            if isinstance(value, bool) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class AuditCostEstimate:
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    pre_discount_usd: float
    estimated_usd: float


def estimate_fable_audit_cost(
    plan: AuditTokenPlan,
    price_card: PriceCard,
) -> AuditCostEstimate:
    """Price a Fable audit before dispatch; price inputs are explicit/configurable."""
    input_tokens = plan.stable_tokens + plan.changed_tokens + plan.cross_boundary_tokens
    cached = min(plan.stable_tokens, math.floor(plan.stable_tokens * plan.cache_hit_ratio))
    uncached = input_tokens - cached
    pre_discount = (
        uncached * price_card.input_per_million
        + cached * price_card.cached_input_per_million
        + plan.expected_output_tokens * price_card.output_per_million
    ) / 1_000_000
    estimated = pre_discount * (1.0 - plan.batch_discount)
    return AuditCostEstimate(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        uncached_input_tokens=uncached,
        output_tokens=plan.expected_output_tokens,
        pre_discount_usd=pre_discount,
        estimated_usd=estimated,
    )


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    tier: RiskTier
    reasons: tuple[str, ...]
    shadow_only: bool = True

    def __post_init__(self) -> None:
        _require_bool("shadow_only", self.shadow_only)
        if not self.shadow_only:
            raise ValueError("risk routing must remain shadow-only until recertified")


HIGH_AUTHORITY_MARKERS = (
    "security",
    "governance",
    "authority",
    "risk",
    "execution",
    "production",
    "capital",
    "credential",
    "secret",
)
SEMANTIC_MARKERS = ("contract", "schema", "semantic", "registry", "identity", "protocol")


def classify_risk_shadow(
    changed_files: Sequence[str],
    *,
    semantic_change: bool = False,
    release_or_production_sensitive: bool = False,
) -> RiskAssessment:
    _require_bool("semantic_change", semantic_change)
    _require_bool("release_or_production_sensitive", release_or_production_sensitive)
    normalized = tuple(path.lower() for path in changed_files)
    reasons: list[str] = []

    if release_or_production_sensitive:
        return RiskAssessment(RiskTier.T4, ("release_or_production_sensitive",))

    authority_hits = sorted(
        marker for marker in HIGH_AUTHORITY_MARKERS if any(marker in path for path in normalized)
    )
    if authority_hits:
        reasons.extend(f"path_marker:{marker}" for marker in authority_hits)
        return RiskAssessment(RiskTier.T3, tuple(reasons))

    semantic_hits = sorted(
        marker for marker in SEMANTIC_MARKERS if any(marker in path for path in normalized)
    )
    if semantic_change or semantic_hits:
        if semantic_change:
            reasons.append("explicit_semantic_change")
        reasons.extend(f"path_marker:{marker}" for marker in semantic_hits)
        return RiskAssessment(RiskTier.T2, tuple(reasons))

    if not normalized or all(
        path.endswith((".md", ".txt", ".json")) or path.startswith("docs/")
        for path in normalized
    ):
        return RiskAssessment(RiskTier.T0, ("mechanical_or_documentation_only",))

    return RiskAssessment(RiskTier.T1, ("local_implementation_change",))


@dataclass(frozen=True, slots=True)
class ModelCallIntent:
    semantic_uncertainty: str
    model_role: str
    candidate_id: str
    required_evidence: tuple[str, ...]
    expected_information_gain: str
    estimated_tokens: int
    estimated_usd: float
    remaining_budget_usd: float
    invalidation_rule: str

    def __post_init__(self) -> None:
        for name in (
            "semantic_uncertainty",
            "model_role",
            "candidate_id",
            "expected_information_gain",
            "invalidation_rule",
        ):
            _require_nonempty(name, getattr(self, name))
        if type(self.estimated_tokens) is not int or self.estimated_tokens <= 0:
            raise ValueError("estimated_tokens must be a positive exact int")
        if self.estimated_usd < 0 or self.remaining_budget_usd < 0:
            raise ValueError("costs must be non-negative")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    action: RouteAction
    reason: str
    shadow_only: bool
    model_role: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty("reason", self.reason)
        _require_bool("shadow_only", self.shadow_only)
        if not self.shadow_only:
            raise ValueError("cost scheduler must remain shadow-only until recertified")


def schedule_model_call_shadow(
    *,
    intent: ModelCallIntent | None,
    deterministic_work_pending: bool,
    external_agent_pending: bool,
    human_authority_required: bool,
) -> RouteDecision:
    for name, value in (
        ("deterministic_work_pending", deterministic_work_pending),
        ("external_agent_pending", external_agent_pending),
        ("human_authority_required", human_authority_required),
    ):
        _require_bool(name, value)

    if human_authority_required:
        return RouteDecision(
            RouteAction.HUMAN_DECISION_REQUIRED,
            "explicit human authority is required",
            True,
        )
    if external_agent_pending:
        return RouteDecision(RouteAction.WAIT, "exact external job remains pending", True)
    if deterministic_work_pending:
        return RouteDecision(
            RouteAction.NO_CALL_REQUIRED,
            "deterministic controller work remains available",
            True,
        )
    if intent is None:
        return RouteDecision(
            RouteAction.NO_CALL_REQUIRED,
            "no unresolved semantic uncertainty requires a model",
            True,
        )
    if intent.estimated_usd > intent.remaining_budget_usd:
        return RouteDecision(
            RouteAction.BUDGET_STOP,
            "estimated call exceeds remaining budget",
            True,
            model_role=intent.model_role,
        )
    return RouteDecision(
        RouteAction.CALL_MODEL,
        "semantic uncertainty is explicit and budget is sufficient",
        True,
        model_role=intent.model_role,
    )


def _packet_with_digest(prefix: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    digest = sha256_json(body)
    body["packet_sha256"] = digest
    body["packet_id"] = f"{prefix}-{digest[:24]}"
    return body


def build_sol_decision_packet(
    *,
    candidate: CandidateIdentity,
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
    return _packet_with_digest(
        "QORE-SOL-PKT",
        {
            "schema_version": "qore.sol.decision.packet.v1",
            "candidate_id": candidate.candidate_id,
            "candidate": asdict(candidate),
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


def build_codex_task_capsule(
    *,
    candidate: CandidateIdentity,
    source_sha: str,
    reference_sha: str | None,
    changed_file_allowlist: Sequence[str],
    forbidden_files: Sequence[str],
    contract: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
    acceptance_tests: Sequence[str],
    source_slices: Sequence[Mapping[str, Any]],
    relevant_tests: Sequence[str],
    historical_delta: Mapping[str, Any],
) -> dict[str, Any]:
    _require_sha40("source_sha", source_sha)
    if reference_sha is not None:
        _require_sha40("reference_sha", reference_sha)
    return _packet_with_digest(
        "QORE-CODEX-CAPS",
        {
            "schema_version": "qore.codex.task.capsule.v1",
            "candidate_id": candidate.candidate_id,
            "candidate": asdict(candidate),
            "source_sha": source_sha,
            "reference_sha": reference_sha,
            "reference_materialized": reference_sha is not None,
            "changed_file_allowlist": list(changed_file_allowlist),
            "forbidden_files": list(forbidden_files),
            "contract": dict(contract),
            "findings": list(findings),
            "acceptance_tests": list(acceptance_tests),
            "source_slices": list(source_slices),
            "relevant_tests": list(relevant_tests),
            "historical_delta": dict(historical_delta),
            "missing_evidence_protocol": "NEED_EVIDENCE(symbol/file/test)",
            "production_authority": False,
        },
    )


def build_review_freeze_package(
    *,
    candidate: CandidateIdentity,
    reviewer_role: str,
    contract_version: str,
    changed_file_manifest: Sequence[Mapping[str, Any]],
    exact_diff: str,
    changed_file_contents: Sequence[Mapping[str, Any]],
    semantic_dependency_slices: Sequence[Mapping[str, Any]],
    architecture_invariants: Sequence[str],
    qg_evidence: Mapping[str, Any],
    adversarial_evidence: Sequence[Mapping[str, Any]],
    prior_finding_closure: Sequence[Mapping[str, Any]],
    questions: Sequence[str],
    prohibited_conclusions: Sequence[str],
) -> dict[str, Any]:
    _require_nonempty("reviewer_role", reviewer_role)
    _require_nonempty("contract_version", contract_version)
    return _packet_with_digest(
        "QORE-REVIEW-FREEZE",
        {
            "schema_version": "qore.review.freeze.package.v1",
            "candidate_id": candidate.candidate_id,
            "candidate": asdict(candidate),
            "reviewer_role": reviewer_role,
            "contract_version": contract_version,
            "changed_file_manifest": list(changed_file_manifest),
            "exact_diff": exact_diff,
            "changed_file_contents": list(changed_file_contents),
            "semantic_dependency_slices": list(semantic_dependency_slices),
            "architecture_invariants": list(architecture_invariants),
            "qg_evidence": dict(qg_evidence),
            "adversarial_evidence": list(adversarial_evidence),
            "prior_finding_closure": list(prior_finding_closure),
            "questions": list(questions),
            "prohibited_conclusions": list(prohibited_conclusions),
            "reviewer_independence": (
                "Prior reviewer verdicts are not authority; reproduce findings independently."
            ),
            "production_authority": False,
        },
    )


@dataclass(frozen=True, slots=True)
class PreauthorizedReviewPlan:
    candidate_id: str
    risk_tier: RiskTier
    stages: tuple[str, ...]
    final_sol_required: bool = True

    def __post_init__(self) -> None:
        _require_nonempty("candidate_id", self.candidate_id)
        _require_bool("final_sol_required", self.final_sol_required)
        if not self.final_sol_required:
            raise ValueError("final Sol adjudication remains mandatory in this policy")


def default_review_plan(candidate_id: str, risk_tier: RiskTier) -> PreauthorizedReviewPlan:
    # Migration-safe policy: reviewer reductions are deliberately NOT activated.
    if risk_tier not in tuple(RiskTier):
        raise ValueError("unsupported risk tier")
    return PreauthorizedReviewPlan(
        candidate_id,
        risk_tier,
        ("QG", "DEEPSEEK_EXPERT", "DEEPSEEK_CODER", "CLAUDE", "SOL_FINAL"),
    )


def clean_pass_next_stage(
    *,
    plan: PreauthorizedReviewPlan,
    completed_stage: str,
    verdict: str,
    exact_candidate_unchanged: bool,
    evidence_complete: bool,
    anomaly_present: bool,
) -> str | None:
    for name, value in (
        ("exact_candidate_unchanged", exact_candidate_unchanged),
        ("evidence_complete", evidence_complete),
        ("anomaly_present", anomaly_present),
    ):
        _require_bool(name, value)
    if verdict not in {"HALLAZGOS: NINGUNO / VALIDACIÓN OK", "CLEAN"}:
        return None
    if not exact_candidate_unchanged or not evidence_complete or anomaly_present:
        return None
    try:
        index = plan.stages.index(completed_stage)
    except ValueError as exc:
        raise ValueError("completed stage is not in preauthorized plan") from exc
    if index + 1 >= len(plan.stages):
        return None
    return plan.stages[index + 1]
