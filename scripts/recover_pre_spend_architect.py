#!/usr/bin/env python3
"""Recover one Autonomous V2 child that failed before any model or agent side effect."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import resume_after_agent_completion as base

ARCHITECT_WORKFLOW_NAME = "QORE Architect autonomous V2"
ARCHITECT_WORKFLOW_PATH = ".github/workflows/qore-architect-autonomous-v2.yml"
PRE_SPEND_RECOVERY_SCHEMA = "qore.architect.pre_spend.recovery.request.v1"
PRE_SPEND_RECOVERY_PATH = "recovery/architect-pre-spend-current.json"
MAX_PRE_SPEND_RECOVERIES_PER_SESSION = 1
SESSION_RE = re.compile(r"^QORE-ORCH-R[1-9][0-9]*$")

REQUIRED_SUCCESS_STEPS = (
    "Enforce trusted ref for execute modes",
    "Checkout orchestrator infrastructure",
    "Verify required credentials without exposing values",
    "Checkout live qore-core read-only",
    "Build canonical QORE state snapshot",
    "Collect Claude and DeepSeek control-plane state",
    "Collect Codex worker run and result state",
)
PRE_SPEND_GATE_STEP = "Validate complete snapshot before model spend"
REQUIRED_SKIPPED_STEPS = (
    "Build bounded model-facing context",
    "Select adaptive Sol reasoning effort",
    "Run GPT-5.6 Sol Principal Architect initial pass",
    "Evaluate one bounded reasoning escalation",
    "Promote or execute one escalated Sol pass",
    "Validate preliminary autonomous decision",
    "Reconstruct and continue one non-terminal Sol step",
    "Validate final autonomous decision",
    "Enforce reconstruction loop guard",
    "Build exact Codex engineering package",
    "Dispatch bounded Codex worker",
    "Refuse to synthesize an undispatched engineering task in execute operation",
    "Build exact Claude or DeepSeek reviewer package",
    "Dispatch to existing reviewer repository",
    "Refuse to synthesize an undispatched review task in autonomous execute operation",
)
FORBIDDEN_ARTIFACT_PREFIXES = (
    "sol-usage",
    "architect-decision",
    "codex-engineering-request",
    "codex-dispatch",
    "reviewer-package",
    "reviewer-dispatch",
)


def _write_receipt(path: str, receipt: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _noop(
    run_id: int,
    attempt: int,
    reason: str,
    *,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": f"CONTROLLER_PRE_SPEND:{run_id}:{attempt}",
        "actor": "CONTROLLER",
        "repository": base.ORCH_REPO,
        "source_run_id": run_id,
        "source_run_attempt": attempt,
        "source_conclusion": "failure",
        "recovery_of_child_architect_run_id": run_id,
        "verified_no_model_or_agent_side_effect": False,
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": reason,
        "production_authority": False,
    }
    if prior is not None:
        for key in (
            "package_id",
            "parent_architect_run_id",
            "session_id",
            "cycle_index",
            "max_auto_resumes",
            "estimated_spend_usd",
            "max_estimated_spend_usd",
            "sol_calls_used",
            "max_sol_calls",
            "sol_calls_reserved_per_architect_run",
            "codex_jobs_used",
            "max_codex_jobs",
            "package_history",
            "pre_spend_recovery_count",
        ):
            if key in prior:
                receipt[key] = prior[key]
    return receipt


def _workflow_run(event: dict[str, Any]) -> dict[str, Any]:
    run = event.get("workflow_run")
    if not isinstance(run, dict):
        raise base.ResumeError("architect workflow_run payload is missing")
    return run


def _job_steps(token: str, run_id: int) -> dict[str, str | None]:
    payload = base.api_json(
        token,
        base.ORCH_API,
        f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise base.ResumeError("architect job evidence is invalid")
    jobs = [
        job
        for job in payload["jobs"]
        if isinstance(job, dict) and job.get("name") == "architect-cycle"
    ]
    if len(jobs) != 1 or jobs[0].get("conclusion") != "failure":
        raise base.ResumeError("pre-spend recovery requires one failed architect-cycle job")
    steps = jobs[0].get("steps")
    if not isinstance(steps, list):
        raise base.ResumeError("architect-cycle step evidence is unavailable")
    result: dict[str, str | None] = {}
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("name"), str):
            result[step["name"]] = step.get("conclusion")
    return result


def _artifact_names(archive: bytes) -> set[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            return {Path(name).name for name in bundle.namelist() if not name.endswith("/")}
    except (zipfile.BadZipFile, OSError) as exc:
        raise base.ResumeError("architect pre-spend artifact is not a valid ZIP") from exc


def validate_pre_spend_failure(
    token: str,
    run_id: int,
    attempt: int,
    expected_head_sha: str | None,
) -> dict[str, Any]:
    run = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise base.ResumeError("failed architect run payload is invalid")
    if (
        run.get("id") != run_id
        or run.get("name") != ARCHITECT_WORKFLOW_NAME
        or run.get("path") != ARCHITECT_WORKFLOW_PATH
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("head_branch") != "main"
        or run.get("run_attempt", 1) != attempt
    ):
        raise base.ResumeError("architect pre-spend recovery source binding failed")
    head_sha = run.get("head_sha")
    if not isinstance(head_sha, str) or base.SHA_RE.fullmatch(head_sha) is None:
        raise base.ResumeError("failed architect HEAD is invalid")
    if expected_head_sha is not None and head_sha != expected_head_sha:
        raise base.ResumeError("failed architect HEAD does not match recovery request")

    steps = _job_steps(token, run_id)
    for name in REQUIRED_SUCCESS_STEPS:
        if steps.get(name) != "success":
            raise base.ResumeError(f"pre-spend recovery refused: prerequisite step was not successful: {name}")
    if steps.get(PRE_SPEND_GATE_STEP) != "failure":
        raise base.ResumeError("architect did not fail at the pre-spend snapshot gate")
    for name in REQUIRED_SKIPPED_STEPS:
        if steps.get(name) != "skipped":
            raise base.ResumeError(f"pre-spend recovery refused: post-gate step was not skipped: {name}")

    archive = base.artifact_bytes(
        token,
        base.ORCH_REPO,
        run_id,
        f"qore-architect-v2-{run_id}",
    )
    names = _artifact_names(archive)
    contaminated = sorted(
        name
        for name in names
        if any(name.startswith(prefix) for prefix in FORBIDDEN_ARTIFACT_PREFIXES)
    )
    if contaminated:
        raise base.ResumeError(
            "pre-spend recovery artifact contains model/agent side-effect evidence: "
            f"{contaminated}"
        )
    return run


def _recovery_receipts(receipts: list[dict[str, Any]], failed_run_id: int) -> list[dict[str, Any]]:
    return [
        receipt
        for receipt in receipts
        if receipt.get("actor") == "CONTROLLER"
        and receipt.get("recovery_of_child_architect_run_id") == failed_run_id
    ]


def _existing_dispatched_recovery(
    receipts: list[dict[str, Any]], failed_run_id: int
) -> dict[str, Any] | None:
    matches = [
        receipt
        for receipt in _recovery_receipts(receipts, failed_run_id)
        if receipt.get("dispatched") is True
    ]
    if len(matches) > 1:
        raise base.ResumeError("multiple pre-spend receipts dispatched for the same failed architect run")
    return matches[0] if matches else None


def _lineage_count(prior: dict[str, Any]) -> int:
    value = prior.get("pre_spend_recovery_count", 0)
    if type(value) is not int or value < 0:
        raise base.ResumeError("prior pre-spend recovery count is invalid")
    return value


def _validate_prior(prior: dict[str, Any]) -> None:
    session_id = prior.get("session_id")
    if not isinstance(session_id, str) or SESSION_RE.fullmatch(session_id) is None:
        raise base.ResumeError("prior receipt session_id is invalid")
    base._nonnegative_int(prior.get("cycle_index"), "prior cycle_index")
    base._decimal_from_receipt(prior.get("estimated_spend_usd"))
    base._nonnegative_int(prior.get("sol_calls_used"), "prior sol_calls_used")
    base._nonnegative_int(prior.get("codex_jobs_used"), "prior codex_jobs_used")
    history = prior.get("package_history")
    if not isinstance(history, list) or not all(isinstance(item, str) for item in history):
        raise base.ResumeError("prior package history is invalid")
    for key in (
        "max_auto_resumes",
        "max_estimated_spend_usd",
        "max_sol_calls",
        "max_codex_jobs",
    ):
        if key not in prior:
            raise base.ResumeError(f"prior receipt lacks bounded-lineage field: {key}")


def _copy_lineage(prior: dict[str, Any], failed_run_id: int, attempt: int) -> dict[str, Any]:
    _validate_prior(prior)
    count = _lineage_count(prior)
    history = list(prior["package_history"])
    return {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": f"CONTROLLER_PRE_SPEND:{failed_run_id}:{attempt}",
        "actor": "CONTROLLER",
        "repository": base.ORCH_REPO,
        "source_run_id": failed_run_id,
        "source_run_attempt": attempt,
        "source_conclusion": "failure",
        "package_id": prior.get("package_id"),
        "parent_architect_run_id": prior.get("parent_architect_run_id"),
        "session_id": prior["session_id"],
        "cycle_index": prior["cycle_index"],
        "max_auto_resumes": prior["max_auto_resumes"],
        "estimated_spend_usd": prior["estimated_spend_usd"],
        "max_estimated_spend_usd": prior["max_estimated_spend_usd"],
        "architect_cost_usd": "0",
        "architect_cost_notes": ["pre_spend_controller_recovery_no_model_call"],
        "agent_cost_usd": "0",
        "agent_cost_kind": "controller_recovery_no_provider_call",
        "sol_calls_used": prior["sol_calls_used"],
        "max_sol_calls": prior["max_sol_calls"],
        "sol_calls_reserved_per_architect_run": prior.get(
            "sol_calls_reserved_per_architect_run",
            base.MAX_SOL_CALLS_PER_ARCHITECT_RUN,
        ),
        "codex_jobs_used": prior["codex_jobs_used"],
        "max_codex_jobs": prior["max_codex_jobs"],
        "package_history": history,
        "pre_spend_recovery_count": count + 1,
        "recovery_of_child_architect_run_id": failed_run_id,
        "verified_no_model_or_agent_side_effect": True,
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": None,
        "production_authority": False,
    }


def _verify_source_resume(
    token: str,
    prior: dict[str, Any],
    source_resume_run_id: int,
) -> None:
    source = base._receipt_for_run(token, source_resume_run_id)
    if not isinstance(source, dict):
        raise base.ResumeError("requested source resume receipt is unavailable")
    for key in (
        "event_key",
        "child_architect_run_id",
        "session_id",
        "cycle_index",
        "estimated_spend_usd",
        "sol_calls_used",
        "codex_jobs_used",
        "package_history",
    ):
        if source.get(key) != prior.get(key):
            raise base.ResumeError("requested source resume receipt does not match live lineage")


def recover(
    token: str,
    receipts: list[dict[str, Any]],
    run_id: int,
    attempt: int,
    expected_head_sha: str | None,
    *,
    source_resume_run_id: int | None = None,
) -> dict[str, Any]:
    prior = base.lineage_for_parent(receipts, run_id)
    if prior is None:
        if source_resume_run_id is not None:
            raise base.ResumeError("requested failed child is not bound by a prior resume receipt")
        return _noop(run_id, attempt, "ARCHITECT_NOT_RESUME_CHILD")
    if source_resume_run_id is not None:
        _verify_source_resume(token, prior, source_resume_run_id)

    existing = _existing_dispatched_recovery(receipts, run_id)
    if existing is not None:
        receipt = _noop(run_id, attempt, "PRE_SPEND_RECOVERY_ALREADY_DISPATCHED", prior=prior)
        receipt["pre_spend_recovery_count"] = max(
            _lineage_count(prior),
            int(existing.get("pre_spend_recovery_count", 1)),
        )
        receipt["existing_child_architect_run_id"] = existing.get("child_architect_run_id")
        return receipt

    count = _lineage_count(prior)
    if count >= MAX_PRE_SPEND_RECOVERIES_PER_SESSION:
        return _noop(run_id, attempt, "PRE_SPEND_RECOVERY_CAP_REACHED", prior=prior)

    validate_pre_spend_failure(token, run_id, attempt, expected_head_sha)
    receipt = _copy_lineage(prior, run_id, attempt)
    child_run_id = base.dispatch_architect(token)
    receipt["dispatched"] = True
    receipt["child_architect_run_id"] = child_run_id
    return receipt


def _load_push_request(event: dict[str, Any], token: str) -> tuple[int, int, str, int]:
    if event.get("ref") != "refs/heads/main":
        raise base.ResumeError("pre-spend recovery push is not on main")
    after = event.get("after")
    if not isinstance(after, str) or base.SHA_RE.fullmatch(after) is None:
        raise base.ResumeError("pre-spend recovery push SHA is invalid")
    if os.environ.get("GITHUB_SHA", "") != after:
        raise base.ResumeError("pre-spend recovery checkout/push SHA mismatch")
    commit = base.api_json(token, base.ORCH_API, f"/commits/{after}")
    files = commit.get("files") if isinstance(commit, dict) else None
    if not isinstance(files, list):
        raise base.ResumeError("pre-spend recovery commit file list is invalid")
    changed = {item.get("filename") for item in files if isinstance(item, dict)}
    if changed != {PRE_SPEND_RECOVERY_PATH}:
        raise base.ResumeError("pre-spend recovery activation commit changed unexpected files")

    request = json.loads(Path(PRE_SPEND_RECOVERY_PATH).read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "failed_child_run_id",
        "failed_child_run_attempt",
        "expected_failed_head_sha",
        "source_resume_run_id",
        "reason",
    }
    if not isinstance(request, dict) or set(request) != allowed:
        raise base.ResumeError("pre-spend recovery request keys are not exact")
    if request.get("schema_version") != PRE_SPEND_RECOVERY_SCHEMA:
        raise base.ResumeError("pre-spend recovery request schema is invalid")
    run_id = request.get("failed_child_run_id")
    attempt = request.get("failed_child_run_attempt")
    head = request.get("expected_failed_head_sha")
    source_resume_run_id = request.get("source_resume_run_id")
    reason = request.get("reason")
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise base.ResumeError("pre-spend recovery run identity is invalid")
    if not isinstance(head, str) or base.SHA_RE.fullmatch(head) is None:
        raise base.ResumeError("pre-spend recovery expected HEAD is invalid")
    if type(source_resume_run_id) is not int or source_resume_run_id <= 0:
        raise base.ResumeError("pre-spend recovery source resume run ID is invalid")
    if not isinstance(reason, str) or not reason.strip():
        raise base.ResumeError("pre-spend recovery reason is empty")
    return run_id, attempt, head, source_resume_run_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise base.ResumeError("GITHUB_TOKEN is required")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise base.ResumeError("GitHub event payload is not an object")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    receipts = base.recent_receipts(token)

    if event_name == "workflow_run":
        run = _workflow_run(event)
        if run.get("path") != ARCHITECT_WORKFLOW_PATH:
            raise base.ResumeError("pre-spend controller received a non-architect workflow_run")
        run_id = run.get("id")
        attempt = run.get("run_attempt", 1)
        if type(run_id) is not int or type(attempt) is not int or attempt <= 0:
            raise base.ResumeError("architect workflow_run identity is invalid")
        if run.get("conclusion") != "failure":
            receipt = _noop(run_id, attempt, "ARCHITECT_COMPLETION_NOT_FAILED")
        else:
            prior = base.lineage_for_parent(receipts, run_id)
            if prior is None:
                receipt = _noop(run_id, attempt, "ARCHITECT_NOT_RESUME_CHILD")
            else:
                try:
                    receipt = recover(
                        token,
                        receipts,
                        run_id,
                        attempt,
                        run.get("head_sha"),
                    )
                except base.ResumeError as exc:
                    receipt = _noop(
                        run_id,
                        attempt,
                        "ARCHITECT_FAILURE_NOT_PRE_SPEND_RECOVERABLE",
                        prior=prior,
                    )
                    receipt["evidence_error"] = str(exc)
    elif event_name == "push":
        run_id, attempt, head, source_resume_run_id = _load_push_request(event, token)
        receipt = recover(
            token,
            receipts,
            run_id,
            attempt,
            head,
            source_resume_run_id=source_resume_run_id,
        )
    else:
        raise base.ResumeError(f"unsupported pre-spend recovery event: {event_name or '<missing>'}")

    _write_receipt(args.output, receipt)
    print(
        "PRE_SPEND_GATE_OK source={} dispatched={} child={} session={} cycle={} sol_calls={} codex_jobs={} stop={}".format(
            receipt.get("source_run_id"),
            receipt.get("dispatched"),
            receipt.get("child_architect_run_id"),
            receipt.get("session_id"),
            receipt.get("cycle_index"),
            receipt.get("sol_calls_used"),
            receipt.get("codex_jobs_used"),
            receipt.get("stop_reason"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (base.ResumeError, json.JSONDecodeError, OSError) as exc:
        print(f"PRE_SPEND_GATE_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(21) from exc
