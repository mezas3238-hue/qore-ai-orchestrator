#!/usr/bin/env python3
"""Explicitly rearm QORE autonomy after an authenticated bounded-stop receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import resume_after_agent_completion as resume

ORCH_REPO = resume.ORCH_REPO
ORCH_API = resume.ORCH_API
RESUME_WORKFLOW_NAME = "QORE agent completion resume"
RESUME_WORKFLOW = resume.RESUME_WORKFLOW
REARM_WORKFLOW = "qore-autonomy-rearm.yml"
REARM_CONFIRMATION = "REARM_BOUNDED_AUTONOMY"
MAX_REARM_SCAN = 40
ALLOWED_STOP_REASONS = {
    "AUTO_RESUME_CYCLE_CAP_REACHED",
    "ESTIMATED_SPEND_CAP_REACHED",
    "SOL_CALL_CAP_REACHED",
    "CODEX_JOB_CAP_REACHED",
}
SESSION_RE = re.compile(r"^QORE-ORCH-R[1-9][0-9]*$")


class RearmError(RuntimeError):
    pass


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise RearmError(f"{label} must be a non-negative integer")
    return value


def _nonnegative_decimal(value: Any, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RearmError(f"{label} must be a non-negative decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise RearmError(f"{label} must be a non-negative finite decimal")
    return parsed


def validate_stopped_receipt(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise RearmError("stopped resume receipt is not an object")
    if receipt.get("schema_version") != "qore.orchestration.resume.receipt.v1":
        raise RearmError("stopped resume receipt schema is invalid")
    if receipt.get("dispatched") is not False or receipt.get("child_architect_run_id") is not None:
        raise RearmError("receipt is not a stopped, undispatched continuation")
    reason = receipt.get("stop_reason")
    if reason not in ALLOWED_STOP_REASONS:
        raise RearmError("stop reason is not eligible for manual budget rearm")
    session_id = receipt.get("session_id")
    if not isinstance(session_id, str) or SESSION_RE.fullmatch(session_id) is None:
        raise RearmError("stopped receipt session_id is invalid")
    _nonnegative_int(receipt.get("cycle_index"), "cycle_index")
    _nonnegative_decimal(receipt.get("estimated_spend_usd"), "estimated_spend_usd")
    _nonnegative_int(receipt.get("sol_calls_used"), "sol_calls_used")
    _nonnegative_int(receipt.get("codex_jobs_used"), "codex_jobs_used")
    history = receipt.get("package_history")
    if not isinstance(history, list) or not history or not all(isinstance(item, str) and item for item in history):
        raise RearmError("stopped receipt package history is invalid")
    if receipt.get("production_authority") is not False:
        raise RearmError("stopped receipt production boundary is invalid")
    return receipt


def validate_resume_run(run: Any, run_id: int) -> None:
    if not isinstance(run, dict) or run.get("id") != run_id:
        raise RearmError("stopped resume workflow run identity is invalid")
    if run.get("name") != RESUME_WORKFLOW_NAME:
        raise RearmError("source run is not the QORE completion resume gate")
    if run.get("event") not in {"workflow_run", "repository_dispatch"}:
        raise RearmError("source resume run was not caused by an agent completion")
    if run.get("status") != "completed":
        raise RearmError("source resume run is not completed")
    if run.get("head_branch") != "main":
        raise RearmError("source resume run did not execute from orchestrator main")
    head_sha = run.get("head_sha")
    if not isinstance(head_sha, str) or resume.SHA_RE.fullmatch(head_sha) is None:
        raise RearmError("source resume run HEAD is invalid")


def load_stopped_receipt(token: str, run_id: int) -> dict[str, Any]:
    run = resume.api_json(token, ORCH_API, f"/actions/runs/{run_id}")
    validate_resume_run(run, run_id)
    archive = resume.artifact_bytes(token, ORCH_REPO, run_id, f"qore-agent-resume-{run_id}")
    receipt = resume.extract_json(archive, "qore-resume-receipt.json")
    return validate_stopped_receipt(receipt)


def active_orchestrator_runs(token: str) -> list[tuple[str, int]]:
    active: list[tuple[str, int]] = []
    for workflow in (resume.ARCHITECT_WORKFLOW, resume.CODEX_WORKFLOW, RESUME_WORKFLOW):
        payload = resume.api_json(token, ORCH_API, f"/actions/workflows/{workflow}/runs?per_page=20")
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise RearmError(f"could not inspect active runs for {workflow}")
        for run in payload["workflow_runs"]:
            if not isinstance(run, dict):
                continue
            if run.get("status") not in {"queued", "in_progress"}:
                continue
            run_id = run.get("id")
            if type(run_id) is not int:
                raise RearmError("active workflow run has invalid ID")
            active.append((workflow, run_id))
    return active


def _rearm_receipt_for_run(token: str, run_id: int) -> dict[str, Any] | None:
    try:
        archive = resume.artifact_bytes(token, ORCH_REPO, run_id, f"qore-autonomy-rearm-{run_id}")
    except resume.ResumeError:
        return None
    value = resume.extract_json(archive, "qore-autonomy-rearm-receipt.json", required=False)
    return value if isinstance(value, dict) else None


def previous_rearms(token: str, stopped_resume_run_id: int) -> list[dict[str, Any]]:
    payload = resume.api_json(
        token,
        ORCH_API,
        f"/actions/workflows/{REARM_WORKFLOW}/runs?per_page={MAX_REARM_SCAN}",
        allow_404=True,
    )
    if payload is None:
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise RearmError("rearm workflow history is invalid")
    matches: list[dict[str, Any]] = []
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict) or run.get("status") != "completed" or type(run.get("id")) is not int:
            continue
        receipt = _rearm_receipt_for_run(token, run["id"])
        if isinstance(receipt, dict) and receipt.get("rearmed_from_resume_run_id") == stopped_resume_run_id:
            matches.append(receipt)
    return matches


def build_rearm_receipt(
    stopped: dict[str, Any],
    *,
    stopped_resume_run_id: int,
    rearm_run_id: int,
    child_architect_run_id: int,
) -> dict[str, Any]:
    validated = validate_stopped_receipt(stopped)
    if stopped_resume_run_id <= 0 or rearm_run_id <= 0 or child_architect_run_id <= 0:
        raise RearmError("rearm run identities must be positive integers")
    return {
        "schema_version": "qore.orchestration.rearm.receipt.v1",
        "rearmed_from_resume_run_id": stopped_resume_run_id,
        "rearm_workflow_run_id": rearm_run_id,
        "prior_session_id": validated["session_id"],
        "prior_stop_reason": validated["stop_reason"],
        "prior_cycle_index": validated["cycle_index"],
        "prior_estimated_spend_usd": str(validated["estimated_spend_usd"]),
        "prior_sol_calls_used": validated["sol_calls_used"],
        "prior_codex_jobs_used": validated["codex_jobs_used"],
        "prior_package_history": list(validated["package_history"]),
        "new_session_seed_architect_run_id": child_architect_run_id,
        "new_session_policy": {
            "max_auto_resumes": resume.DEFAULT_MAX_AUTO_RESUMES,
            "max_estimated_spend_usd": str(resume.DEFAULT_MAX_ESTIMATED_SPEND_USD),
            "max_sol_calls": resume.DEFAULT_MAX_SOL_CALLS,
            "max_codex_jobs": resume.DEFAULT_MAX_CODEX_JOBS,
        },
        "dispatched": True,
        "production_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stopped-resume-run-id", type=int, required=True)
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.stopped_resume_run_id <= 0:
        raise RearmError("stopped resume run ID must be positive")
    if args.confirmation != REARM_CONFIRMATION:
        raise RearmError("explicit bounded-autonomy rearm confirmation is missing")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RearmError("rearm may only originate from an explicit workflow_dispatch")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RearmError("rearm controller must execute from orchestrator main")
    rearm_run_raw = os.environ.get("GITHUB_RUN_ID", "")
    if not rearm_run_raw.isdigit() or int(rearm_run_raw) <= 0:
        raise RearmError("rearm workflow run ID is invalid")
    rearm_run_id = int(rearm_run_raw)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RearmError("GITHUB_TOKEN is required")

    stopped = load_stopped_receipt(token, args.stopped_resume_run_id)
    prior = previous_rearms(token, args.stopped_resume_run_id)
    if prior:
        raise RearmError("this exact stopped receipt has already been rearmed")
    active = [item for item in active_orchestrator_runs(token) if item[1] != rearm_run_id]
    if active:
        raise RearmError(f"autonomy already has active orchestrator work: {active}")

    child_run_id = resume.dispatch_architect(token)
    receipt = build_rearm_receipt(
        stopped,
        stopped_resume_run_id=args.stopped_resume_run_id,
        rearm_run_id=rearm_run_id,
        child_architect_run_id=child_run_id,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "AUTONOMY_REARM_OK prior_session={} stopped_run={} child_architect_run={}".format(
            receipt["prior_session_id"],
            args.stopped_resume_run_id,
            child_run_id,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RearmError, resume.ResumeError) as exc:
        print(f"AUTONOMY_REARM_ERROR: {exc}")
        raise SystemExit(31) from exc
