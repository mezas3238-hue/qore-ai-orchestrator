#!/usr/bin/env python3
"""Build reviewer packages from exact frozen QG summaries plus live CI identity.

The source QG summary must already be present in the immutable architect snapshot
and bound to the same PR/BASE/HEAD/SYNTHETIC. This module never trusts that
summary alone: the referenced QORE CI run and quality job are re-fetched live
and must still match the exact candidate and completed/success contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import build_reviewer_package as package

QG_KEYS = {
    "run_id",
    "job_id",
    "ruff_passed",
    "mypy_source_files",
    "pytest_collected",
    "pytest_passed",
    "pytest_warnings",
    "coverage_total_statements",
    "coverage_missed_statements",
    "coverage_percent",
}


class FrozenQGError(RuntimeError):
    pass


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise FrozenQGError(f"{label} is invalid")
    return value


def validate_qg_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != QG_KEYS:
        raise FrozenQGError("frozen QG summary keys are not exact")
    if value.get("ruff_passed") is not True:
        raise FrozenQGError("frozen QG does not prove Ruff PASS")
    run_id = _positive_int(value.get("run_id"), "QG run_id")
    job_id = _positive_int(value.get("job_id"), "QG job_id")
    mypy_files = _positive_int(value.get("mypy_source_files"), "Mypy source count")
    collected = _positive_int(value.get("pytest_collected"), "pytest collected count")
    passed = _positive_int(value.get("pytest_passed"), "pytest passed count")
    warnings = _positive_int(value.get("pytest_warnings"), "pytest warning count", allow_zero=True)
    total = _positive_int(value.get("coverage_total_statements"), "coverage total")
    missed = _positive_int(value.get("coverage_missed_statements"), "coverage missed", allow_zero=True)
    percent = _positive_int(value.get("coverage_percent"), "coverage percent", allow_zero=True)
    if collected != passed:
        raise FrozenQGError("frozen QG pytest count is not all-pass")
    if missed > total or percent > 100:
        raise FrozenQGError("frozen QG coverage values are invalid")
    return {
        "run_id": run_id,
        "job_id": job_id,
        "ruff_passed": True,
        "mypy_source_files": mypy_files,
        "pytest_collected": collected,
        "pytest_passed": passed,
        "pytest_warnings": warnings,
        "coverage_total_statements": total,
        "coverage_missed_statements": missed,
        "coverage_percent": percent,
    }


def _candidate_requests(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    external = snapshot.get("external_reviewer_state")
    if not isinstance(external, dict):
        raise FrozenQGError("snapshot lacks external reviewer state")
    values: list[dict[str, Any]] = []
    for actor in ("deepseek", "claude"):
        state = external.get(actor)
        if not isinstance(state, dict):
            continue
        request = state.get("current_request")
        if isinstance(request, dict):
            values.append(request)
    return values


def resolve_frozen_summary(
    snapshot: dict[str, Any],
    *,
    pr_number: int,
    base: str,
    head: str,
    synthetic: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for request in _candidate_requests(snapshot):
        if (
            request.get("pr_number") == pr_number
            and request.get("expected_base") == base
            and request.get("expected_head") == head
            and request.get("expected_synthetic") == synthetic
            and isinstance(request.get("qg_summary"), dict)
        ):
            matches.append(validate_qg_summary(request["qg_summary"]))
    if not matches:
        raise FrozenQGError("no exact frozen reviewer QG summary matches the live freeze")
    canonical = json.dumps(matches[0], sort_keys=True, separators=(",", ":"))
    for value in matches[1:]:
        if json.dumps(value, sort_keys=True, separators=(",", ":")) != canonical:
            raise FrozenQGError("conflicting exact-freeze QG summaries exist in snapshot")
    return matches[0]


def validate_live_qg_identity(summary: dict[str, Any], *, head: str) -> None:
    run_id = summary["run_id"]
    job_id = summary["job_id"]
    run = package.api_json(f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise FrozenQGError("live QG run payload is invalid")
    if not (
        run.get("id") == run_id
        and run.get("workflow_id") == package.QORE_CI_WORKFLOW_ID
        and run.get("name") == package.QORE_CI_WORKFLOW_NAME
        and run.get("path") == package.QORE_CI_WORKFLOW_PATH
        and run.get("event") == "pull_request"
        and run.get("head_sha") == head
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    ):
        raise FrozenQGError("live QG run no longer matches exact successful QORE CI")

    jobs = package.api_json(f"/actions/runs/{run_id}/jobs", {"per_page": "100"})
    if not isinstance(jobs, dict) or not isinstance(jobs.get("jobs"), list):
        raise FrozenQGError("live QG jobs payload is invalid")
    matches = [
        job
        for job in jobs["jobs"]
        if isinstance(job, dict)
        and job.get("id") == job_id
        and job.get("run_id") == run_id
        and job.get("name") == "quality"
        and job.get("head_sha") == head
        and job.get("status") == "completed"
        and job.get("conclusion") == "success"
    ]
    if len(matches) != 1:
        raise FrozenQGError("live quality job no longer matches exact successful QG identity")


def quality_from_snapshot(
    snapshot: dict[str, Any],
    *,
    pr_number: int,
    base: str,
    head: str,
    synthetic: str,
) -> package.QualitySummary:
    summary = resolve_frozen_summary(
        snapshot,
        pr_number=pr_number,
        base=base,
        head=head,
        synthetic=synthetic,
    )
    validate_live_qg_identity(summary, head=head)
    return package.QualitySummary(**summary)


def _snapshot_arg(argv: list[str]) -> Path:
    indexes = [index for index, value in enumerate(argv) if value == "--snapshot"]
    if len(indexes) != 1 or indexes[0] + 1 >= len(argv):
        raise FrozenQGError("review package invocation lacks unique --snapshot")
    return Path(argv[indexes[0] + 1])


def main() -> int:
    try:
        snapshot_path = _snapshot_arg(sys.argv[1:])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise FrozenQGError("review snapshot is not an object")
    except (OSError, UnicodeError, json.JSONDecodeError, FrozenQGError) as exc:
        raise SystemExit(f"REVIEW_PACKAGE_BLOCKED: {exc}") from exc

    original_resolve_quality = package.resolve_quality

    def frozen_resolve_quality(head: str, synthetic: str) -> package.QualitySummary:
        # resolve_freeze runs immediately before this hook in package.main(), so
        # derive the exact live BASE/PR identity from the snapshot only after
        # requiring the snapshot's PR entry to agree with this HEAD/SYNTHETIC.
        candidates = [
            pr
            for pr in snapshot.get("open_pull_requests", [])
            if isinstance(pr, dict)
            and pr.get("head_sha") == head
            and pr.get("synthetic_sha") == synthetic
        ]
        if len(candidates) != 1:
            raise package.PackageError("snapshot lacks unique exact live PR freeze for frozen QG")
        pr = candidates[0]
        pr_number = pr.get("number")
        base = pr.get("base_sha")
        if type(pr_number) is not int or not isinstance(base, str):
            raise package.PackageError("snapshot exact PR identity is incomplete")
        try:
            return quality_from_snapshot(
                snapshot,
                pr_number=pr_number,
                base=base,
                head=head,
                synthetic=synthetic,
            )
        except FrozenQGError as exc:
            raise package.PackageError(str(exc)) from exc

    package.resolve_quality = frozen_resolve_quality
    try:
        return package.main()
    finally:
        package.resolve_quality = original_resolve_quality


if __name__ == "__main__":
    raise SystemExit(main())
