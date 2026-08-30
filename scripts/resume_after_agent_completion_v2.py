#!/usr/bin/env python3
"""Run the agent-completion resume gate with exact agent/recovery lineage binding."""

from __future__ import annotations

import argparse
import json
import os
import re
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
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _completion_payload(event: dict[str, Any]) -> tuple[str, str, int, int, str]:
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or payload.get("schema_version") != "qore.agent.completion.v1":
        raise base.ResumeError("agent callback schema is invalid")
    repo = payload.get("repository")
    actor = payload.get("actor")
    run_id = payload.get("workflow_run_id")
    attempt = payload.get("workflow_run_attempt")
    package_id = payload.get("package_id")
    if (
        not isinstance(repo, str)
        or not isinstance(actor, str)
        or type(run_id) is not int
        or type(attempt) is not int
        or attempt <= 0
        or not isinstance(package_id, str)
    ):
        raise base.ResumeError("agent callback identity is invalid")
    return repo, actor, run_id, attempt, package_id


def parse_reviewer_event(event: dict[str, Any], reviewer_token: str) -> dict[str, Any]:
    repo, actor, run_id, attempt, package_id = _completion_payload(event)
    if repo not in base.ALLOWED_REVIEWERS or actor != base.ALLOWED_REVIEWERS[repo]:
        raise base.ResumeError("reviewer callback actor/repository is not allowlisted")
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


def _codex_worker_job_completed(token: str, run_id: int) -> None:
    payload = base.api_json(
        token,
        base.ORCH_API,
        f"/actions/runs/{run_id}/jobs?filter=latest&per_page=100",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise base.ResumeError("Codex worker job evidence is invalid")
    workers = [
        job
        for job in payload["jobs"]
        if isinstance(job, dict) and job.get("name") == "worker"
    ]
    if len(workers) != 1:
        raise base.ResumeError("expected exactly one Codex worker job")
    worker = workers[0]
    if worker.get("status") != "completed" or worker.get("conclusion") != "success":
        raise base.ResumeError("Codex worker job is not exact completed success")


def parse_codex_repository_event(event: dict[str, Any], token: str) -> dict[str, Any]:
    repo, actor, run_id, attempt, package_id = _completion_payload(event)
    if repo != base.ORCH_REPO or actor != "CODEX":
        raise base.ResumeError("Codex callback actor/repository is not trusted")
    if base.PACKAGE_RE.fullmatch(package_id) is None:
        raise base.ResumeError("Codex callback package is invalid")

    live = base.api_json(token, base.ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(live, dict):
        raise base.ResumeError("Codex live run payload is invalid")
    if live.get("id") != run_id or live.get("run_attempt", 1) != attempt:
        raise base.ResumeError("Codex callback run identity mismatch")
    if live.get("path") != CODEX_WORKFLOW_PATH or live.get("event") != "workflow_dispatch":
        raise base.ResumeError("Codex callback run origin is not trusted")
    if live.get("head_branch") != "main":
        raise base.ResumeError("Codex callback did not execute from orchestrator main")
    status = live.get("status")
    conclusion = live.get("conclusion")
    if status not in {"in_progress", "completed"}:
        raise base.ResumeError("Codex callback source run has invalid status")
    if status == "completed" and conclusion != "success":
        raise base.ResumeError("completed Codex callback source run is not successful")
    if status == "in_progress" and conclusion is not None:
        raise base.ResumeError("in-progress Codex callback source run has a conclusion")
    head_sha = live.get("head_sha")
    if not isinstance(head_sha, str) or base.SHA_RE.fullmatch(head_sha) is None:
        raise base.ResumeError("Codex callback source run head SHA is invalid")
    run_package = base.codex_package_from_title(live.get("display_title"))
    if run_package != package_id:
        raise base.ResumeError("Codex callback run title/package binding failed")

    _codex_worker_job_completed(token, run_id)
    archive = base.artifact_bytes(token, base.ORCH_REPO, run_id, f"qore-codex-worker-{run_id}")
    request = base.extract_json(archive, "codex-request.json")
    result = base.extract_json(archive, "codex-worker-result.json")
    assert request is not None and result is not None
    if request.get("schema_version") != "qore.codex.engineering.request.v1" or request.get("package_id") != package_id:
        raise base.ResumeError("Codex callback artifact request/package binding failed")
    if result.get("schema_version") != "qore.codex.worker.result.v1":
        raise base.ResumeError("Codex callback worker result schema is invalid")
    if result.get("production_authority") is not False:
        raise base.ResumeError("Codex callback worker result grants forbidden Production authority")
    source_sha = request.get("source_main_sha")
    if not isinstance(source_sha, str) or base.SHA_RE.fullmatch(source_sha) is None:
        raise base.ResumeError("Codex callback request source_main_sha is invalid")
    if result.get("source_main_sha") != source_sha:
        raise base.ResumeError("Codex callback result/source SHA binding failed")
    parent_raw = str(request.get("architect_run_id") or "")
    if not parent_raw.isdigit() or int(parent_raw) <= 0:
        raise base.ResumeError("Codex callback request lacks valid architect_run_id")
    usage = base.extract_json(archive, "codex-worker-usage.json", required=False)
    if usage is not None:
        worker_cost = base.estimate_usage_cost(usage)
        worker_cost_kind = "observed"
    else:
        worker_cost = base.UNKNOWN_CODEX_COST_RESERVE_USD
        worker_cost_kind = "reserved_missing_usage"
    return {
        "actor": "CODEX",
        "repo": base.ORCH_REPO,
        "run_id": run_id,
        "run_attempt": attempt,
        "package_id": package_id,
        "parent_architect_run_id": int(parent_raw),
        "source_main_sha": source_sha,
        "run_head_sha": head_sha,
        "conclusion": conclusion if isinstance(conclusion, str) else "worker_job_success_callback_pending",
        "agent_cost_usd": worker_cost,
        "agent_cost_kind": worker_cost_kind,
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
    """Return canonical lineage parent, package archive, and cost archive.

    A normal reviewer package is owned directly by an Autonomous V2 run. A package
    emitted by the bounded reviewer-recovery workflow is owned by that recovery run,
    but its budget/session lineage must remain anchored to the original Autonomous V2
    run proven by reviewer-recovery-source.json and the original artifact digest.
    """
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

    if event_name == "workflow_run":
        completion = base.parse_codex_event(event, token)
        package_archive = base.architect_archive(token, completion["parent_architect_run_id"])
        cost_archive = package_archive
    elif event_name == "repository_dispatch":
        payload = event.get("client_payload") if isinstance(event.get("client_payload"), dict) else {}
        if payload.get("actor") == "CODEX":
            completion = parse_codex_repository_event(event, token)
            package_archive = base.architect_archive(token, completion["parent_architect_run_id"])
            cost_archive = package_archive
        else:
            reviewer_token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
            if not reviewer_token:
                raise base.ResumeError("QORE_REVIEWER_DISPATCH_TOKEN is required for reviewer callback verification")
            completion = parse_reviewer_event(event, reviewer_token)
            package_parent = completion["package_parent_architect_run_id"]
            canonical_parent, package_archive, cost_archive = resolve_reviewer_parent(token, package_parent)
            completion["parent_architect_run_id"] = canonical_parent
    elif event_name == "workflow_dispatch" and args.mode == "dry_run":
        output = {
            "schema_version": "qore.orchestration.resume.receipt.v1",
            "event_key": f"DIAGNOSTIC:{os.environ.get('GITHUB_RUN_ID', 'unknown')}",
            "dispatched": False,
            "stop_reason": "MANUAL_DRY_RUN_NO_AGENT_COMPLETION",
            "production_authority": False,
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("AGENT_RESUME_DIAGNOSTIC_OK")
        return 0
    else:
        raise base.ResumeError(f"unsupported completion event: {event_name or '<missing>'}")

    base.parent_package_binding(package_archive, completion)
    sol_cost, sol_notes, sol_calls = base.architect_cost(cost_archive)
    receipts = base.recent_receipts(token)
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
    if receipt.get("stop_reason") is None:
        child_run_id = base.dispatch_architect(token)
        receipt["dispatched"] = True
        receipt["child_architect_run_id"] = child_run_id
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
