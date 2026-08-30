#!/usr/bin/env python3
"""Recover one failed Autonomous V2 Sol pass after spend but before any delegated side effect."""

from __future__ import annotations

import argparse
import io
import json
import os
import zipfile
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any

import resume_after_agent_completion as base

ARCHITECT_WORKFLOW_NAME = "QORE Architect autonomous V2"
ARCHITECT_WORKFLOW_PATH = ".github/workflows/qore-architect-autonomous-v2.yml"
REARM_WORKFLOW_PATH = ".github/workflows/qore-autonomy-rearm.yml"
REQUEST_SCHEMA = "qore.architect.post_spend.recovery.request.v1"
REQUEST_PATH = "recovery/architect-post-spend-current.json"
CONFIRMATION = "RECOVER_INCOMPLETE_SOL"
MAX_POST_SPEND_RECOVERIES_PER_SESSION = 1
RECOVERABLE_INCOMPLETE_REASONS = {"max_tokens", "max_output_tokens"}
# Run 33338976459 predates SOL-INCOMPLETE-HARDENING-044 and therefore lacks the
# usage/incomplete-details artifact that the hardened runner now always writes.
LEGACY_INCOMPLETE_RUNS = {
    33338976459: "63447020ddfef8c857739c3fd45c63894c041dc5",
}

REQUIRED_SUCCESS_STEPS = (
    "Enforce trusted ref for execute modes",
    "Checkout orchestrator infrastructure",
    "Verify required credentials without exposing values",
    "Checkout live qore-core read-only",
    "Build canonical QORE state snapshot",
    "Collect Claude and DeepSeek control-plane state",
    "Collect Codex worker run and result state",
    "Validate complete snapshot before model spend",
    "Build bounded model-facing context",
    "Select adaptive Sol reasoning effort",
)
SOL_STEP = "Run GPT-5.6 Sol Principal Architect initial pass"
REQUIRED_SKIPPED_STEPS = (
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


def _artifact_names(archive: bytes) -> set[str]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            return {Path(name).name for name in bundle.namelist() if not name.endswith("/")}
    except (zipfile.BadZipFile, OSError) as exc:
        raise base.ResumeError("post-spend architect artifact is not a valid ZIP") from exc


def _job_steps(token: str, run_id: int) -> dict[str, str | None]:
    payload = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise base.ResumeError("architect job evidence is invalid")
    jobs = [job for job in payload["jobs"] if isinstance(job, dict) and job.get("name") == "architect-cycle"]
    if len(jobs) != 1 or jobs[0].get("conclusion") != "failure":
        raise base.ResumeError("post-spend recovery requires one failed architect-cycle job")
    steps = jobs[0].get("steps")
    if not isinstance(steps, list):
        raise base.ResumeError("architect-cycle step evidence is unavailable")
    return {
        step["name"]: step.get("conclusion")
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }


def _rearm_receipt(token: str, run_id: int, failed_run_id: int) -> dict[str, Any]:
    run = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise base.ResumeError("source rearm run is invalid")
    if (
        run.get("id") != run_id
        or run.get("path") != REARM_WORKFLOW_PATH
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("head_branch") != "main"
    ):
        raise base.ResumeError("source rearm run binding failed")
    archive = base.artifact_bytes(token, base.ORCH_REPO, run_id, f"qore-autonomy-rearm-{run_id}")
    receipt = base.extract_json(archive, "qore-autonomy-rearm-receipt.json")
    assert receipt is not None
    if (
        receipt.get("schema_version") != "qore.orchestration.rearm.receipt.v1"
        or receipt.get("production_authority") is not False
        or receipt.get("dispatched") is not True
        or receipt.get("new_session_seed_architect_run_id") != failed_run_id
        or receipt.get("rearm_workflow_run_id") != run_id
    ):
        raise base.ResumeError("source rearm receipt does not bind the failed architect seed")
    policy = receipt.get("new_session_policy")
    if not isinstance(policy, dict):
        raise base.ResumeError("source rearm receipt lacks bounded session policy")
    expected = {
        "max_auto_resumes": base.DEFAULT_MAX_AUTO_RESUMES,
        "max_estimated_spend_usd": str(base.DEFAULT_MAX_ESTIMATED_SPEND_USD),
        "max_sol_calls": base.DEFAULT_MAX_SOL_CALLS,
        "max_codex_jobs": base.DEFAULT_MAX_CODEX_JOBS,
    }
    if policy != expected:
        raise base.ResumeError("source rearm session policy does not match controller bounds")
    return receipt


def validate_failed_architect(token: str, run_id: int) -> tuple[dict[str, Any], Decimal, list[str]]:
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
        or run.get("run_attempt", 1) != 1
    ):
        raise base.ResumeError("post-spend architect source binding failed")
    head_sha = run.get("head_sha")
    if not isinstance(head_sha, str) or base.SHA_RE.fullmatch(head_sha) is None:
        raise base.ResumeError("failed architect HEAD is invalid")

    steps = _job_steps(token, run_id)
    for name in REQUIRED_SUCCESS_STEPS:
        if steps.get(name) != "success":
            raise base.ResumeError(f"post-spend recovery refused: prerequisite step was not successful: {name}")
    if steps.get(SOL_STEP) != "failure":
        raise base.ResumeError("architect did not fail in the initial Sol pass")
    for name in REQUIRED_SKIPPED_STEPS:
        if steps.get(name) != "skipped":
            raise base.ResumeError(f"post-spend recovery refused: delegated/post-Sol step was not skipped: {name}")

    archive = base.artifact_bytes(token, base.ORCH_REPO, run_id, f"qore-architect-v2-{run_id}")
    names = _artifact_names(archive)
    contaminated = sorted(
        name for name in names if any(name.startswith(prefix) for prefix in FORBIDDEN_ARTIFACT_PREFIXES)
    )
    if contaminated:
        raise base.ResumeError(f"post-spend recovery artifact contains delegated/decision evidence: {contaminated}")

    usage = base.extract_json(archive, "sol-usage-initial.json", required=False)
    notes: list[str] = []
    if usage is not None:
        if usage.get("response_status") != "incomplete":
            raise base.ResumeError("post-spend recovery requires an incomplete Sol usage record")
        reason = usage.get("incomplete_reason")
        if reason not in RECOVERABLE_INCOMPLETE_REASONS:
            raise base.ResumeError("Sol incomplete reason is not allowlisted for automatic recovery")
        cost = base.estimate_usage_cost(usage)
        notes.append(f"observed_incomplete_sol:{reason}")
    else:
        if LEGACY_INCOMPLETE_RUNS.get(run_id) != head_sha:
            raise base.ResumeError("failed Sol run lacks hardened usage evidence and is not the pinned legacy migration case")
        cost = base.UNKNOWN_SOL_PASS_RESERVE_USD
        notes.append("reserved_pinned_legacy_incomplete_sol_without_usage")
    return run, cost.quantize(Decimal("0.000001"), rounding=ROUND_UP), notes


def _active_architect_or_codex(token: str) -> bool:
    for workflow in (base.ARCHITECT_WORKFLOW, base.CODEX_WORKFLOW):
        payload = base.api_json(token, base.ORCH_API, f"/actions/workflows/{workflow}/runs?per_page=20")
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            raise base.ResumeError("active-work scan is invalid")
        if any(
            isinstance(run, dict) and run.get("status") in {"queued", "in_progress"}
            for run in payload["workflow_runs"]
        ):
            return True
    return False


def _existing_recovery(receipts: list[dict[str, Any]], run_id: int) -> dict[str, Any] | None:
    matches = [
        receipt for receipt in receipts
        if receipt.get("actor") == "CONTROLLER_POST_SPEND"
        and receipt.get("recovery_of_child_architect_run_id") == run_id
        and receipt.get("dispatched") is True
    ]
    if len(matches) > 1:
        raise base.ResumeError("multiple post-spend recoveries claim the same failed architect")
    return matches[0] if matches else None


def _receipt(
    failed_run_id: int,
    source_rearm_run_id: int,
    cost: Decimal,
    notes: list[str],
) -> dict[str, Any]:
    session_id = f"QORE-ORCH-R{failed_run_id}"
    return {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": f"CONTROLLER_POST_SPEND:{failed_run_id}:1",
        "actor": "CONTROLLER_POST_SPEND",
        "repository": base.ORCH_REPO,
        "source_run_id": failed_run_id,
        "source_run_attempt": 1,
        "source_conclusion": "failure",
        "source_rearm_run_id": source_rearm_run_id,
        "package_id": None,
        "parent_architect_run_id": failed_run_id,
        "session_id": session_id,
        "cycle_index": 0,
        "max_auto_resumes": base.DEFAULT_MAX_AUTO_RESUMES,
        "estimated_spend_usd": str(cost),
        "max_estimated_spend_usd": str(base.DEFAULT_MAX_ESTIMATED_SPEND_USD),
        "architect_cost_usd": str(cost),
        "architect_cost_notes": notes,
        "agent_cost_usd": "0",
        "agent_cost_kind": "controller_post_spend_recovery",
        "sol_calls_used": 1,
        "max_sol_calls": base.DEFAULT_MAX_SOL_CALLS,
        "sol_calls_reserved_per_architect_run": base.MAX_SOL_CALLS_PER_ARCHITECT_RUN,
        "codex_jobs_used": 0,
        "max_codex_jobs": base.DEFAULT_MAX_CODEX_JOBS,
        "package_history": [],
        "pre_spend_recovery_count": 0,
        "post_spend_recovery_count": 1,
        "recovery_of_child_architect_run_id": failed_run_id,
        "verified_no_agent_side_effect": True,
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": None,
        "production_authority": False,
    }


def recover(token: str, failed_run_id: int, source_rearm_run_id: int) -> dict[str, Any]:
    _rearm_receipt(token, source_rearm_run_id, failed_run_id)
    _, cost, notes = validate_failed_architect(token, failed_run_id)
    receipts = base.recent_receipts(token)
    existing = _existing_recovery(receipts, failed_run_id)
    receipt = _receipt(failed_run_id, source_rearm_run_id, cost, notes)
    if existing is not None:
        receipt["stop_reason"] = "POST_SPEND_RECOVERY_ALREADY_DISPATCHED"
        receipt["existing_child_architect_run_id"] = existing.get("child_architect_run_id")
        return receipt
    session_matches = [
        item for item in receipts
        if item.get("actor") == "CONTROLLER_POST_SPEND" and item.get("session_id") == receipt["session_id"]
    ]
    if len(session_matches) >= MAX_POST_SPEND_RECOVERIES_PER_SESSION:
        receipt["stop_reason"] = "POST_SPEND_RECOVERY_CAP_REACHED"
        return receipt
    reserve = base.UNKNOWN_SOL_PASS_RESERVE_USD * base.MAX_SOL_CALLS_PER_ARCHITECT_RUN
    if cost + reserve > base.DEFAULT_MAX_ESTIMATED_SPEND_USD:
        receipt["stop_reason"] = "ESTIMATED_SPEND_CAP_REACHED"
        return receipt
    if 1 + base.MAX_SOL_CALLS_PER_ARCHITECT_RUN > base.DEFAULT_MAX_SOL_CALLS:
        receipt["stop_reason"] = "SOL_CALL_CAP_REACHED"
        return receipt
    if _active_architect_or_codex(token):
        receipt["stop_reason"] = "ACTIVE_WORK_PRESENT"
        return receipt
    child = base.dispatch_architect(token)
    receipt["dispatched"] = True
    receipt["child_architect_run_id"] = child
    return receipt


def _load_request(event: dict[str, Any], token: str) -> tuple[int, int]:
    if event.get("ref") != "refs/heads/main":
        raise base.ResumeError("post-spend recovery push is not on main")
    after = event.get("after")
    if not isinstance(after, str) or base.SHA_RE.fullmatch(after) is None:
        raise base.ResumeError("post-spend recovery push SHA is invalid")
    if os.environ.get("GITHUB_SHA", "") != after:
        raise base.ResumeError("post-spend recovery checkout/push SHA mismatch")
    commit = base.api_json(token, base.ORCH_API, f"/commits/{after}")
    files = commit.get("files") if isinstance(commit, dict) else None
    if not isinstance(files, list):
        raise base.ResumeError("post-spend recovery commit file list is invalid")
    changed = {item.get("filename") for item in files if isinstance(item, dict)}
    if changed != {REQUEST_PATH}:
        raise base.ResumeError("post-spend recovery activation commit changed files beyond the one-shot request")
    request = json.loads(Path(REQUEST_PATH).read_text(encoding="utf-8"))
    if (
        request.get("schema_version") != REQUEST_SCHEMA
        or request.get("confirmation") != CONFIRMATION
        or request.get("production_authority") is not False
    ):
        raise base.ResumeError("post-spend recovery request contract is invalid")
    failed = request.get("failed_architect_run_id")
    rearm = request.get("source_rearm_run_id")
    if type(failed) is not int or failed <= 0 or type(rearm) is not int or rearm <= 0:
        raise base.ResumeError("post-spend recovery request run IDs are invalid")
    return failed, rearm


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN is not configured.", file=sys.stderr)
        return 2
    try:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        failed_run_id, source_rearm_run_id = _load_request(event, token)
        receipt = recover(token, failed_run_id, source_rearm_run_id)
        _write_receipt(args.output, receipt)
    except (base.ResumeError, json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"Post-spend architect recovery failed closed: {exc}", file=sys.stderr)
        return 3
    if receipt.get("dispatched") is True:
        print(
            "ARCHITECT_POST_SPEND_RECOVERY_OK source={} child={} sol_calls={} spend={}".format(
                failed_run_id,
                receipt["child_architect_run_id"],
                receipt["sol_calls_used"],
                receipt["estimated_spend_usd"],
            )
        )
        return 0
    print(f"ARCHITECT_POST_SPEND_RECOVERY_STOP reason={receipt.get('stop_reason')}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
