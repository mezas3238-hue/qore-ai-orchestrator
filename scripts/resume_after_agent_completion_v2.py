#!/usr/bin/env python3
"""Run the agent-completion resume gate with exact reviewer and recovery lineage binding."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import resume_after_agent_completion as base

REVIEWER_WORKFLOW_PATHS = {
    "mezas3238-hue/qore-claude-reviewer": ".github/workflows/claude-qore-review.yml",
    "mezas3238-hue/qore-deepseek-reviewer": ".github/workflows/deepseek-qore-review.yml",
}
ARCHITECT_WORKFLOW_PATH = ".github/workflows/qore-architect-autonomous-v2.yml"
CODEX_WORKFLOW_PATH = ".github/workflows/codex-engineer-worker.yml"
RECOVERY_WORKFLOW_PATH = ".github/workflows/qore-architect-review-recovery-v1.yml"
RECOVERY_SOURCE_SCHEMA = "qore.reviewer.dispatch.recovery.source.v1"
PRE_SPEND_RECOVERY_SCHEMA = "qore.architect.pre_spend.recovery.request.v1"
PRE_SPEND_RECOVERY_PATH = "recovery/architect-pre-spend-current.json"
MAX_PRE_SPEND_RECOVERIES = 1
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PRE_SPEND_SKIPPED_STEPS = (
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
FORBIDDEN_PRE_SPEND_ARTIFACTS = (
    "sol-usage",
    "architect-decision",
    "codex-engineering-request",
    "codex-dispatch",
    "reviewer-package",
    "reviewer-dispatch",
)


def parse_reviewer_event(event: dict[str, Any], reviewer_token: str) -> dict[str, Any]:
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or payload.get("schema_version") != "qore.agent.completion.v1":
        raise base.ResumeError("reviewer callback schema is invalid")
    repo = payload.get("repository")
    actor = payload.get("actor")
    if repo not in base.ALLOWED_REVIEWERS or actor != base.ALLOWED_REVIEWERS[repo]:
        raise base.ResumeError("reviewer callback actor/repository is not allowlisted")
    run_id = payload.get("workflow_run_id")
    attempt = payload.get("workflow_run_attempt")
    package_id = payload.get("package_id")
    if type(run_id) is not int or type(attempt) is not int or attempt <= 0 or not isinstance(package_id, str):
        raise base.ResumeError("reviewer callback identity is invalid")
    package_pattern = base.REVIEWER_PACKAGE_RES.get(actor)
    if package_pattern is None or package_pattern.fullmatch(package_id) is None:
        raise base.ResumeError("reviewer callback package does not match actor contract")

    package_parent_run_id = base.reviewer_parent_run(package_id)
    api = f"https://api.github.com/repos/{repo}"
    run = base.api_json(reviewer_token, api, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise base.ResumeError("reviewer live run payload is invalid")
    base._validate_run_identity(run, run_id, attempt)
    workflow_name = base.REVIEWER_WORKFLOW_NAMES[repo]
    workflow_path = REVIEWER_WORKFLOW_PATHS[repo]
    if run.get("path") != workflow_path or run.get("event") != "workflow_dispatch":
        raise base.ResumeError("reviewer run origin is not trusted")
    if run.get("head_branch") != "main":
        raise base.ResumeError("reviewer run did not execute from reviewer main")
    run_package = base.reviewer_package_from_title(run.get("display_title"), workflow_name, actor)
    if run_package != package_id:
        raise base.ResumeError("reviewer run title/package binding failed")
    conclusion = run.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise base.ResumeError("reviewer conclusion is invalid")
    head_sha = run.get("head_sha")
    encoded = base.urllib.parse.quote("requests/current.json", safe="/")
    request_payload = base.api_json(reviewer_token, api, f"/contents/{encoded}?ref={head_sha}")
    request = base._decode_content(request_payload)
    if request.get("package_id") != package_id:
        raise base.ResumeError("reviewer run HEAD does not contain the callback package")
    expected_head = request.get("expected_head")
    if not isinstance(expected_head, str) or base.SHA_RE.fullmatch(expected_head) is None:
        raise base.ResumeError("reviewer request expected_head is invalid")
    return {
        "actor": actor,
        "repo": repo,
        "run_id": run_id,
        "run_attempt": attempt,
        "package_id": package_id,
        "package_parent_architect_run_id": package_parent_run_id,
        "parent_architect_run_id": package_parent_run_id,
        "source_main_sha": expected_head,
        "run_head_sha": head_sha,
        "conclusion": conclusion,
        "agent_cost_usd": Decimal("0"),
        "agent_cost_kind": "provider_usage_not_in_orchestrator",
    }


def _exact_artifact_metadata(token: str, run_id: int, name: str) -> dict[str, Any]:
    payload = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise base.ResumeError("architect artifact list is invalid")
    matches = [
        item
        for item in payload["artifacts"]
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is False
        and type(item.get("id")) is int
    ]
    if len(matches) != 1:
        raise base.ResumeError(f"expected exactly one architect artifact {name!r}; found {len(matches)}")
    return matches[0]


def resolve_reviewer_parent(
    token: str,
    package_parent_run_id: int,
) -> tuple[int, bytes, bytes]:
    """Return canonical lineage parent, package archive, and cost archive."""
    run = base.api_json(token, base.ORCH_API, f"/actions/runs/{package_parent_run_id}")
    if not isinstance(run, dict):
        raise base.ResumeError("reviewer package parent run payload is invalid")
    if run.get("status") != "completed" or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        raise base.ResumeError("reviewer package parent run origin is not trusted")

    if run.get("path") == ARCHITECT_WORKFLOW_PATH and run.get("name") == "QORE Architect autonomous V2":
        archive = base.architect_archive(token, package_parent_run_id)
        return package_parent_run_id, archive, archive

    if run.get("path") != RECOVERY_WORKFLOW_PATH or run.get("conclusion") != "success":
        raise base.ResumeError("reviewer package parent is neither Autonomous V2 nor a successful bounded recovery")

    recovery_artifact_name = f"qore-architect-v2-{package_parent_run_id}"
    recovery_archive = base.artifact_bytes(token, base.ORCH_REPO, package_parent_run_id, recovery_artifact_name)
    source = base.extract_json(recovery_archive, "reviewer-recovery-source.json")
    if source is None or source.get("schema_version") != RECOVERY_SOURCE_SCHEMA:
        raise base.ResumeError("reviewer recovery source evidence is invalid")
    source_run_id = source.get("source_architect_run_id")
    source_head_sha = source.get("source_architect_head_sha")
    source_artifact_id = source.get("source_artifact_id")
    source_artifact_name = source.get("source_artifact_name")
    source_artifact_digest = source.get("source_artifact_digest")
    if type(source_run_id) is not int or source_run_id <= 0:
        raise base.ResumeError("reviewer recovery source architect run ID is invalid")
    if not isinstance(source_head_sha, str) or base.SHA_RE.fullmatch(source_head_sha) is None:
        raise base.ResumeError("reviewer recovery source architect HEAD is invalid")
    if source_artifact_name != f"qore-architect-v2-{source_run_id}":
        raise base.ResumeError("reviewer recovery source artifact name is invalid")
    if type(source_artifact_id) is not int or source_artifact_id <= 0:
        raise base.ResumeError("reviewer recovery source artifact ID is invalid")
    if not isinstance(source_artifact_digest, str) or DIGEST_RE.fullmatch(source_artifact_digest) is None:
        raise base.ResumeError("reviewer recovery source artifact digest is invalid")

    source_run = base.api_json(token, base.ORCH_API, f"/actions/runs/{source_run_id}")
    if not isinstance(source_run, dict):
        raise base.ResumeError("canonical source architect run payload is invalid")
    if (
        source_run.get("name") != "QORE Architect autonomous V2"
        or source_run.get("path") != ARCHITECT_WORKFLOW_PATH
        or source_run.get("event") != "workflow_dispatch"
        or source_run.get("status") != "completed"
        or source_run.get("head_branch") != "main"
        or source_run.get("head_sha") != source_head_sha
    ):
        raise base.ResumeError("canonical source architect run binding failed")

    metadata = _exact_artifact_metadata(token, source_run_id, source_artifact_name)
    if metadata.get("id") != source_artifact_id or metadata.get("digest") != source_artifact_digest:
        raise base.ResumeError("canonical source architect artifact identity/digest mismatch")
    source_archive = base.artifact_bytes(token, base.ORCH_REPO, source_run_id, source_artifact_name)
    return source_run_id, recovery_archive, source_archive


def _workflow_run_payload(event: dict[str, Any]) -> dict[str, Any]:
    run = event.get("workflow_run")
    if not isinstance(run, dict):
        raise base.ResumeError("workflow_run event payload is missing")
    return run


def _run_steps(token: str, run_id: int) -> dict[str, str | None]:
    payload = base.api_json(
        token,
        base.ORCH_API,
        f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise base.ResumeError("architect job evidence is invalid")
    jobs = [job for job in payload["jobs"] if isinstance(job, dict) and job.get("name") == "architect-cycle"]
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


def _validate_pre_spend_failure(
    token: str,
    run_id: int,
    run_attempt: int,
    expected_head_sha: str | None,
) -> dict[str, Any]:
    run = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise base.ResumeError("failed architect run payload is invalid")
    if (
        run.get("id") != run_id
        or run.get("name") != "QORE Architect autonomous V2"
        or run.get("path") != ARCHITECT_WORKFLOW_PATH
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("head_branch") != "main"
        or run.get("run_attempt", 1) != run_attempt
    ):
        raise base.ResumeError("architect pre-spend recovery source binding failed")
    head_sha = run.get("head_sha")
    if not isinstance(head_sha, str) or base.SHA_RE.fullmatch(head_sha) is None:
        raise base.ResumeError("failed architect HEAD is invalid")
    if expected_head_sha is not None and head_sha != expected_head_sha:
        raise base.ResumeError("failed architect HEAD does not match recovery request")

    steps = _run_steps(token, run_id)
    if steps.get("Validate complete snapshot before model spend") != "failure":
        raise base.ResumeError("architect did not fail at the pre-spend snapshot gate")
    for name in PRE_SPEND_SKIPPED_STEPS:
        if steps.get(name) != "skipped":
            raise base.ResumeError(f"pre-spend recovery refused: step was not skipped: {name}")

    archive = base.artifact_bytes(token, base.ORCH_REPO, run_id, f"qore-architect-v2-{run_id}")
    names = _artifact_names(archive)
    contaminated = sorted(
        name
        for name in names
        if any(name.startswith(prefix) for prefix in FORBIDDEN_PRE_SPEND_ARTIFACTS)
    )
    if contaminated:
        raise base.ResumeError(f"pre-spend recovery artifact contains spend/side-effect evidence: {contaminated}")
    return run


def _pre_spend_noop(run_id: int, run_attempt: int, reason: str) -> dict[str, Any]:
    return {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": f"CONTROLLER_PRE_SPEND:{run_id}:{run_attempt}",
        "actor": "CONTROLLER",
        "repository": base.ORCH_REPO,
        "source_run_id": run_id,
        "source_run_attempt": run_attempt,
        "source_conclusion": "failure",
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": reason,
        "production_authority": False,
    }


def _copy_lineage_receipt(prior: dict[str, Any], failed_run_id: int, failed_attempt: int) -> dict[str, Any]:
    count = prior.get("pre_spend_recovery_count", 0)
    if type(count) is not int or count < 0:
        raise base.ResumeError("prior pre-spend recovery count is invalid")
    if count >= MAX_PRE_SPEND_RECOVERIES:
        return {
            **_pre_spend_noop(failed_run_id, failed_attempt, "PRE_SPEND_RECOVERY_CAP_REACHED"),
            "session_id": prior.get("session_id"),
            "cycle_index": prior.get("cycle_index"),
            "estimated_spend_usd": prior.get("estimated_spend_usd"),
            "sol_calls_used": prior.get("sol_calls_used"),
            "codex_jobs_used": prior.get("codex_jobs_used"),
            "package_history": prior.get("package_history", []),
            "pre_spend_recovery_count": count,
        }
    required = (
        "session_id",
        "cycle_index",
        "estimated_spend_usd",
        "sol_calls_used",
        "codex_jobs_used",
        "package_history",
        "max_auto_resumes",
        "max_estimated_spend_usd",
        "max_sol_calls",
        "max_codex_jobs",
    )
    if any(key not in prior for key in required):
        raise base.ResumeError("prior receipt lacks bounded-lineage fields")
    history = prior.get("package_history")
    if not isinstance(history, list) or not all(isinstance(item, str) for item in history):
        raise base.ResumeError("prior package history is invalid")
    return {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": f"CONTROLLER_PRE_SPEND:{failed_run_id}:{failed_attempt}",
        "actor": "CONTROLLER",
        "repository": base.ORCH_REPO,
        "source_run_id": failed_run_id,
        "source_run_attempt": failed_attempt,
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
            "sol_calls_reserved_per_architect_run", base.MAX_SOL_CALLS_PER_ARCHITECT_RUN
        ),
        "codex_jobs_used": prior["codex_jobs_used"],
        "max_codex_jobs": prior["max_codex_jobs"],
        "package_history": list(history),
        "pre_spend_recovery_count": count + 1,
        "recovery_of_child_architect_run_id": failed_run_id,
        "verified_no_model_or_agent_side_effect": True,
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": None,
        "production_authority": False,
    }


def _load_push_recovery_request(event: dict[str, Any], token: str) -> tuple[int, int, str, int]:
    if event.get("ref") != "refs/heads/main":
        raise base.ResumeError("pre-spend recovery push is not on main")
    after = event.get("after")
    if not isinstance(after, str) or base.SHA_RE.fullmatch(after) is None:
        raise base.ResumeError("pre-spend recovery push SHA is invalid")
    live_sha = os.environ.get("GITHUB_SHA", "")
    if live_sha != after:
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


def _handle_pre_spend_recovery(
    token: str,
    receipts: list[dict[str, Any]],
    run_id: int,
    attempt: int,
    expected_head: str | None,
    *,
    source_resume_run_id: int | None = None,
) -> dict[str, Any]:
    prior = base.lineage_for_parent(receipts, run_id)
    if prior is None:
        if source_resume_run_id is not None:
            raise base.ResumeError("requested pre-spend recovery child is not bound by a prior resume receipt")
        return _pre_spend_noop(run_id, attempt, "ARCHITECT_NOT_RESUME_CHILD")
    if source_resume_run_id is not None:
        source_receipt = base._receipt_for_run(token, source_resume_run_id)
        if not isinstance(source_receipt, dict):
            raise base.ResumeError("requested source resume receipt is unavailable")
        for key in ("event_key", "child_architect_run_id", "session_id", "cycle_index"):
            if source_receipt.get(key) != prior.get(key):
                raise base.ResumeError("requested source resume receipt does not match live lineage")
    _validate_pre_spend_failure(token, run_id, attempt, expected_head)
    receipt = _copy_lineage_receipt(prior, run_id, attempt)
    if receipt.get("stop_reason") is None:
        child_run_id = base.dispatch_architect(token)
        receipt["dispatched"] = True
        receipt["child_architect_run_id"] = child_run_id
    return receipt


def _preserve_pre_spend_count(receipt: dict[str, Any], receipts: list[dict[str, Any]], parent_run_id: int) -> None:
    prior = base.lineage_for_parent(receipts, parent_run_id)
    count = 0 if prior is None else prior.get("pre_spend_recovery_count", 0)
    if type(count) is not int or count < 0:
        raise base.ResumeError("pre-spend recovery count in lineage is invalid")
    receipt["pre_spend_recovery_count"] = count


def _write_receipt(path: str, receipt: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--mode", choices=["dry_run", "execute"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-auto-resumes", type=int, default=base.DEFAULT_MAX_AUTO_RESUMES)
    parser.add_argument("--max-estimated-spend-usd", default=str(base.DEFAULT_MAX_ESTIMATED_SPEND_USD))
    parser.add_argument("--max-sol-calls", type=int, default=base.DEFAULT_MAX_SOL_CALLS)
    parser.add_argument("--max-codex-jobs", type=int, default=base.DEFAULT_MAX_CODEX_JOBS)
    args = parser.parse_args()
    if args.max_auto_resumes < 1 or args.max_auto_resumes > 12:
        raise base.ResumeError("max-auto-resumes must be between 1 and 12")
    try:
        max_spend = Decimal(args.max_estimated_spend_usd)
    except Exception as exc:
        raise base.ResumeError("max-estimated-spend-usd is invalid") from exc
    if max_spend <= 0 or max_spend > Decimal("25.00"):
        raise base.ResumeError("max-estimated-spend-usd must be > 0 and <= 25")
    if args.max_sol_calls < base.MAX_SOL_CALLS_PER_ARCHITECT_RUN or args.max_sol_calls > 36:
        raise base.ResumeError("max-sol-calls must be between 3 and 36")
    if args.max_codex_jobs < 1 or args.max_codex_jobs > 12:
        raise base.ResumeError("max-codex-jobs must be between 1 and 12")

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise base.ResumeError("GITHUB_TOKEN is required")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise base.ResumeError("GitHub event payload is not an object")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    receipts = base.recent_receipts(token)

    if event_name == "push":
        run_id, attempt, expected_head, source_resume_run_id = _load_push_recovery_request(event, token)
        receipt = _handle_pre_spend_recovery(
            token,
            receipts,
            run_id,
            attempt,
            expected_head,
            source_resume_run_id=source_resume_run_id,
        )
        _write_receipt(args.output, receipt)
        print(
            f"PRE_SPEND_RECOVERY_OK source={run_id} dispatched={receipt.get('dispatched')} "
            f"child={receipt.get('child_architect_run_id')} stop={receipt.get('stop_reason')}"
        )
        return 0

    if event_name == "workflow_run":
        workflow_run = _workflow_run_payload(event)
        if workflow_run.get("path") == ARCHITECT_WORKFLOW_PATH:
            run_id = workflow_run.get("id")
            attempt = workflow_run.get("run_attempt", 1)
            if type(run_id) is not int or type(attempt) is not int or attempt <= 0:
                raise base.ResumeError("architect workflow_run identity is invalid")
            if workflow_run.get("conclusion") != "failure":
                receipt = _pre_spend_noop(run_id, attempt, "ARCHITECT_COMPLETION_NOT_FAILED")
            else:
                prior = base.lineage_for_parent(receipts, run_id)
                if prior is None:
                    receipt = _pre_spend_noop(run_id, attempt, "ARCHITECT_NOT_RESUME_CHILD")
                else:
                    try:
                        receipt = _handle_pre_spend_recovery(
                            token, receipts, run_id, attempt, workflow_run.get("head_sha")
                        )
                    except base.ResumeError as exc:
                        receipt = {
                            **_pre_spend_noop(run_id, attempt, "ARCHITECT_FAILURE_NOT_PRE_SPEND_RECOVERABLE"),
                            "evidence_error": str(exc),
                            "session_id": prior.get("session_id"),
                            "cycle_index": prior.get("cycle_index"),
                        }
            _write_receipt(args.output, receipt)
            print(
                f"ARCHITECT_COMPLETION_GATE_OK source={run_id} dispatched={receipt.get('dispatched')} "
                f"stop={receipt.get('stop_reason')}"
            )
            return 0
        completion = base.parse_codex_event(event, token)
        package_archive = base.architect_archive(token, completion["parent_architect_run_id"])
        cost_archive = package_archive
    elif event_name == "repository_dispatch":
        reviewer_token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
        if not reviewer_token:
            raise base.ResumeError("QORE_REVIEWER_DISPATCH_TOKEN is required for reviewer callback verification")
        completion = parse_reviewer_event(event, reviewer_token)
        package_parent = completion["package_parent_architect_run_id"]
        canonical_parent, package_archive, cost_archive = resolve_reviewer_parent(token, package_parent)
        completion["parent_architect_run_id"] = canonical_parent
    elif event_name == "workflow_dispatch" and args.mode == "dry_run":
        receipt = {
            "schema_version": "qore.orchestration.resume.receipt.v1",
            "event_key": f"DIAGNOSTIC:{os.environ.get('GITHUB_RUN_ID', 'unknown')}",
            "dispatched": False,
            "stop_reason": "MANUAL_DRY_RUN_NO_AGENT_COMPLETION",
            "production_authority": False,
        }
        _write_receipt(args.output, receipt)
        print("AGENT_RESUME_DIAGNOSTIC_OK")
        return 0
    else:
        raise base.ResumeError(f"unsupported completion event: {event_name or '<missing>'}")

    base.parent_package_binding(package_archive, completion)
    sol_cost, sol_notes, sol_calls = base.architect_cost(cost_archive)
    receipt = base.build_receipt(
        completion,
        receipts,
        sol_cost,
        sol_notes,
        mode=args.mode,
        max_auto_resumes=args.max_auto_resumes,
        max_spend=max_spend,
        architect_sol_calls=sol_calls,
        max_sol_calls=args.max_sol_calls,
        max_codex_jobs=args.max_codex_jobs,
    )
    _preserve_pre_spend_count(receipt, receipts, completion["parent_architect_run_id"])
    if receipt.get("stop_reason") is None:
        child_run_id = base.dispatch_architect(token)
        receipt["dispatched"] = True
        receipt["child_architect_run_id"] = child_run_id
    _write_receipt(args.output, receipt)
    print(
        "AGENT_RESUME_OK actor={} package={} dispatched={} session={} cycle={} spend={} sol_calls={} codex_jobs={} stop={}".format(
            receipt.get("actor"),
            receipt.get("package_id"),
            receipt.get("dispatched"),
            receipt.get("session_id"),
            receipt.get("cycle_index"),
            receipt.get("estimated_spend_usd"),
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
        print(f"AGENT_RESUME_ERROR: {exc}", file=base.sys.stderr)
        raise SystemExit(21)
