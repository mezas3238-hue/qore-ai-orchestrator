from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence

from fable_audit_control import compact_fable_findings


class FableProviderAdapter(Protocol):
    """Provider adapter boundary; concrete network implementation lives outside policy logic."""

    def audit(self, package: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class FableExecutionResult:
    package_id: str
    execution_mode: str
    raw_finding_count: int
    compacted_findings: Mapping[str, Sequence[Mapping[str, Any]]]
    provider_usage: Mapping[str, Any]
    final_sol_adjudication_required: bool = True
    reviewer_substitution: bool = False
    production_authority: bool = False

    def __post_init__(self) -> None:
        if self.reviewer_substitution:
            raise ValueError("Fable may not substitute mandatory reviewers")
        if not self.final_sol_adjudication_required:
            raise ValueError("Fable findings require final Sol adjudication")
        if self.production_authority:
            raise ValueError("production_authority must remain false")


def execute_fable_audit(
    *,
    package: Mapping[str, Any],
    adapter: FableProviderAdapter,
) -> FableExecutionResult:
    if package.get("schema_version") != "qore.fable.audit.package.v2":
        raise ValueError("unexpected executable Fable package schema")
    mode = package.get("execution_mode")
    if mode not in {"CONTROLLED_VALIDATION", "LIMITED_LIVE"}:
        raise ValueError("Fable provider call requires controlled or limited-live execution mode")
    if package.get("production_authority") is not False or package.get("reviewer_substitution") is not False:
        raise ValueError("Fable package attempted forbidden authority")
    preflight = package.get("cost_preflight")
    if not isinstance(preflight, Mapping) or preflight.get("within_budget") is not True:
        raise ValueError("Fable provider call requires passing deterministic cost preflight")

    raw = adapter.audit(package)
    if not isinstance(raw, Mapping):
        raise ValueError("Fable provider adapter result must be an object")
    findings = raw.get("findings", [])
    usage = raw.get("usage", {})
    if not isinstance(findings, list) or any(not isinstance(item, Mapping) for item in findings):
        raise ValueError("Fable findings must be an array of objects")
    if not isinstance(usage, Mapping):
        raise ValueError("Fable provider usage must be an object")
    compacted = compact_fable_findings(findings)
    return FableExecutionResult(
        package_id=str(package.get("package_id") or ""),
        execution_mode=str(mode),
        raw_finding_count=len(findings),
        compacted_findings=compacted,
        provider_usage=dict(usage),
        final_sol_adjudication_required=True,
        reviewer_substitution=False,
        production_authority=False,
    )


def result_json(result: FableExecutionResult) -> dict[str, Any]:
    value = asdict(result)
    value["compacted_findings"] = {
        key: list(items) for key, items in result.compacted_findings.items()
    }
    return {
        "schema_version": "qore.fable.execution.result.v1",
        **value,
    }
