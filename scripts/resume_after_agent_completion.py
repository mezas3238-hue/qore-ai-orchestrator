#!/usr/bin/env python3
"""Resume QORE Sol after an exact completed agent run, with bounded spend and loop guards."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any

ORCH_REPO = "mezas3238-hue/qore-ai-orchestrator"
ORCH_API = f"https://api.github.com/repos/{ORCH_REPO}"
CODEX_WORKFLOW_NAME = "QORE Codex engineer worker"
CODEX_WORKFLOW = "codex-engineer-worker.yml"
ARCHITECT_WORKFLOW = "qore-architect-autonomous-v2.yml"
RESUME_WORKFLOW = "qore-agent-completion-resume.yml"
ALLOWED_REVIEWERS = {
    "mezas3238-hue/qore-claude-reviewer": "CLAUDE_CODE",
    "mezas3238-hue/qore-deepseek-reviewer": "DEEPSEEK",
}
REVIEWER_WORKFLOW_NAMES = {
    "mezas3238-hue/qore-claude-reviewer": "Claude QORE review",
    "mezas3238-hue/qore-deepseek-reviewer": "DeepSeek QORE review",
}
REVIEWER_PACKAGE_RES = {
    "CLAUDE_CODE": re.compile(r"^QORE-SOL-[0-9a-f]{12}-CLAUDE-R(?P<run_id>[1-9][0-9]*)$"),
    "DEEPSEEK": re.compile(r"^QORE-SOL-[0-9a-f]{12}-DS-(?:EXPERT|CODER)-R(?P<run_id>[1-9][0-9]*)$"),
}
PACKAGE_RE = re.compile(r"^QORE-CODEX-[0-9a-f]{12}-[0-9a-f]{16}$")
REVIEW_PACKAGE_RE = re.compile(r"^QORE-SOL-[0-9a-f]{12}-(?:CLAUDE|DS-(?:EXPERT|CODER))-R(?P<run_id>[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = "qore-ai-orchestrator/1.0"
MAX_ZIP_BYTES = 4_000_000
MAX_JSON_BYTES = 300_000
MAX_RECEIPT_SCAN = 40
DEFAULT_MAX_AUTO_RESUMES = 3
DEFAULT_MAX_ESTIMATED_SPEND_USD = Decimal("5.00")
DEFAULT_MAX_SOL_CALLS = 12
DEFAULT_MAX_CODEX_JOBS = 3
MAX_SOL_CALLS_PER_ARCHITECT_RUN = 3
UNKNOWN_CODEX_COST_RESERVE_USD = Decimal("1.90")
UNKNOWN_SOL_PASS_RESERVE_USD = Decimal("1.25")

# Current regular token prices on 2026-08-30, USD / 1M text tokens.
# Pricing is deliberately controller-owned and pinned; changing it requires code review.
PRICE_TABLE = {
    "gpt-5.6-sol": {
        "input": Decimal("4.00"),
        "cached": Decimal("0.40"),
        "cache_write": Decimal("5.00"),
        "output": Decimal("20.00"),
    },
    "gpt-5.3-codex": {
        "input": Decimal("1.75"),
        "cached": Decimal("0.175"),
        "cache_write": Decimal("2.1875"),
        "output": Decimal("14.00"),
    },
}


class ResumeError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def api_json(
    token: str,
    base: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        raise ResumeError(f"GitHub API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ResumeError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def api_status(
    token: str,
    base: str,
    path: str,
    *,
    method: str,
    payload: dict[str, Any],
) -> int:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ResumeError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def download_artifact(token: str, repo: str, artifact_id: int) -> bytes:
    api = f"https://api.github.com/repos/{repo}"
    request = urllib.request.Request(f"{api}/actions/artifacts/{artifact_id}/zip", headers=_headers(token))
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=45)
        data = response.read(MAX_ZIP_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise ResumeError(f"artifact download failed with HTTP {exc.code}") from exc
        location = exc.headers.get("Location")
        if not location:
            raise ResumeError("artifact redirect lacks Location") from exc
        try:
            with urllib.request.urlopen(location, timeout=60) as redirected:
                data = redirected.read(MAX_ZIP_BYTES + 1)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as redirected_exc:
            raise ResumeError(f"signed artifact download failed: {type(redirected_exc).__name__}") from redirected_exc
    if len(data) > MAX_ZIP_BYTES:
        raise ResumeError("artifact exceeds hard ZIP size bound")
    return data


def artifact_bytes(token: str, repo: str, run_id: int, name: str) -> bytes:
    api = f"https://api.github.com/repos/{repo}"
    payload = api_json(token, api, f"/actions/runs/{run_id}/artifacts?per_page=100")
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise ResumeError("artifact list is invalid")
    matches = [
        item
        for item in payload["artifacts"]
        if isinstance(item, dict)
        and item.get("name") == name
        and item.get("expired") is False
        and type(item.get("id")) is int
    ]
    if len(matches) != 1:
        raise ResumeError(f"expected exactly one non-expired artifact {name!r}; found {len(matches)}")
    return download_artifact(token, repo, matches[0]["id"])


def extract_json(archive_bytes: bytes, basename: str, *, required: bool = True) -> dict[str, Any] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = [name for name in archive.namelist() if Path(name).name == basename]
            if not names:
                if required:
                    raise ResumeError(f"artifact lacks required {basename}")
                return None
            if len(names) != 1:
                raise ResumeError(f"artifact contains multiple {basename}")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_JSON_BYTES:
                raise ResumeError(f"{basename} exceeds hard JSON size bound")
            value = json.loads(archive.read(info).decode("utf-8"))
    except ResumeError:
        raise
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise ResumeError(f"could not decode {basename}") from exc
    if not isinstance(value, dict):
        raise ResumeError(f"{basename} is not an object")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ResumeError(f"{label} must be a non-negative integer")
    return value


def estimate_usage_cost(usage: dict[str, Any]) -> Decimal:
    model = str(usage.get("model") or "")
    rates = PRICE_TABLE.get(model)
    if rates is None:
        raise ResumeError(f"unpriced model in usage evidence: {model or '<missing>'}")
    input_tokens = _nonnegative_int(usage.get("input_tokens", 0), "input_tokens")
    cached = _nonnegative_int(usage.get("cached_tokens", 0), "cached_tokens")
    cache_write = _nonnegative_int(usage.get("cache_write_tokens", 0), "cache_write_tokens")
    output = _nonnegative_int(usage.get("output_tokens", 0), "output_tokens")
    if cached + cache_write > input_tokens:
        raise ResumeError("cached + cache-write tokens exceed input tokens")
    uncached = input_tokens - cached - cache_write
    million = Decimal(1_000_000)
    cost = (
        Decimal(uncached) * rates["input"]
        + Decimal(cached) * rates["cached"]
        + Decimal(cache_write) * rates["cache_write"]
        + Decimal(output) * rates["output"]
    ) / million
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_UP)


def codex_package_from_title(title: Any) -> str:
    prefix = "Codex worker · "
    value = str(title or "")
    package = value[len(prefix):].strip() if value.startswith(prefix) else ""
    if not PACKAGE_RE.fullmatch(package):
        raise ResumeError("Codex workflow title is not bound to an exact package")
    return package


def reviewer_parent_run(package_id: str) -> int:
    match = REVIEW_PACKAGE_RE.fullmatch(package_id)
    if match is None:
        raise ResumeError("reviewer package ID is not an orchestrator package")
    return int(match.group("run_id"))


def reviewer_package_from_title(title: Any, workflow_name: str, actor: str) -> str:
    pattern = REVIEWER_PACKAGE_RES.get(actor)
    if pattern is None:
        raise ResumeError("reviewer actor has no package contract")
    prefix = f"{workflow_name} · "
    value = str(title or "")
    package_id = value[len(prefix):].strip() if value.startswith(prefix) else ""
    if pattern.fullmatch(package_id) is None:
        raise ResumeError("reviewer workflow title is not bound to an exact package")
    return package_id


def event_key(actor: str, repo: str, run_id: int, attempt: int) -> str:
    return f"{actor}:{repo}:{run_id}:{attempt}"


def _validate_run_identity(run: dict[str, Any], run_id: int, attempt: int) -> None:
    if run.get("id") != run_id:
        raise ResumeError("completion run ID mismatch")
    if run.get("status") != "completed":
        raise ResumeError("completion source run is not completed")
    observed_attempt = run.get("run_attempt", 1)
    if observed_attempt != attempt:
        raise ResumeError("completion run attempt mismatch")
    head_sha = run.get("head_sha")
    if not isinstance(head_sha, str) or SHA_RE.fullmatch(head_sha) is None:
        raise ResumeError("completion source run head SHA is invalid")


def parse_codex_event(event: dict[str, Any], token: str) -> dict[str, Any]:
    run = event.get("workflow_run")
    if not isinstance(run, dict):
        raise ResumeError("workflow_run payload missing")
    if run.get("name") != CODEX_WORKFLOW_NAME:
        raise ResumeError("workflow_run is not the bounded Codex worker")
    run_id = run.get("id")
    attempt = run.get("run_attempt", 1)
    if type(run_id) is not int or type(attempt) is not int or attempt <= 0:
        raise ResumeError("Codex run identity is invalid")
    live = api_json(token, ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(live, dict):
        raise ResumeError("Codex live run payload is invalid")
    _validate_run_identity(live, run_id, attempt)
    if live.get("name") != CODEX_WORKFLOW_NAME or live.get("event") != "workflow_dispatch":
        raise ResumeError("Codex run origin is not trusted")
    if live.get("head_branch") != "main":
        raise ResumeError("Codex worker did not run from orchestrator main")
    package_id = codex_package_from_title(live.get("display_title"))
    archive = artifact_bytes(token, ORCH_REPO, run_id, f"qore-codex-worker-{run_id}")
    request = extract_json(archive, "codex-request.json")
    assert request is not None
    if request.get("schema_version") != "qore.codex.engineering.request.v1" or request.get("package_id") != package_id:
        raise ResumeError("Codex artifact request/package binding failed")
    parent_raw = str(request.get("architect_run_id") or "")
    if not parent_raw.isdigit() or int(parent_raw) <= 0:
        raise ResumeError("Codex request lacks valid architect_run_id")
    source_sha = request.get("source_main_sha")
    if not isinstance(source_sha, str) or SHA_RE.fullmatch(source_sha) is None:
        raise ResumeError("Codex request source_main_sha is invalid")
    usage = extract_json(archive, "codex-worker-usage.json", required=False)
    if usage is not None:
        worker_cost = estimate_usage_cost(usage)
        worker_cost_kind = "observed"
    else:
        worker_cost = UNKNOWN_CODEX_COST_RESERVE_USD
        worker_cost_kind = "reserved_missing_usage"
    return {
        "actor": "CODEX",
        "repo": ORCH_REPO,
        "run_id": run_id,
        "run_attempt": attempt,
        "package_id": package_id,
        "parent_architect_run_id": int(parent_raw),
        "source_main_sha": source_sha,
        "run_head_sha": live["head_sha"],
        "conclusion": live.get("conclusion"),
        "agent_cost_usd": worker_cost,
        "agent_cost_kind": worker_cost_kind,
    }


def _decode_content(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        raise ResumeError("reviewer request content is unavailable")
    try:
        encoded = "".join(payload["content"].split())
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        value = json.loads(decoded)
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResumeError("reviewer request content is invalid") from exc
    if not isinstance(value, dict):
        raise ResumeError("reviewer request is not an object")
    return value


def parse_reviewer_event(event: dict[str, Any], reviewer_token: str) -> dict[str, Any]:
    payload = event.get("client_payload")
    if not isinstance(payload, dict) or payload.get("schema_version") != "qore.agent.completion.v1":
        raise ResumeError("reviewer callback schema is invalid")
    repo = payload.get("repository")
    actor = payload.get("actor")
    if repo not in ALLOWED_REVIEWERS or actor != ALLOWED_REVIEWERS[repo]:
        raise ResumeError("reviewer callback actor/repository is not allowlisted")
    run_id = payload.get("workflow_run_id")
    attempt = payload.get("workflow_run_attempt")
    package_id = payload.get("package_id")
    if type(run_id) is not int or type(attempt) is not int or attempt <= 0 or not isinstance(package_id, str):
        raise ResumeError("reviewer callback identity is invalid")
    package_pattern = REVIEWER_PACKAGE_RES.get(actor)
    if package_pattern is None or package_pattern.fullmatch(package_id) is None:
        raise ResumeError("reviewer callback package does not match actor contract")
    parent_run_id = reviewer_parent_run(package_id)
    api = f"https://api.github.com/repos/{repo}"
    run = api_json(reviewer_token, api, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise ResumeError("reviewer live run payload is invalid")
    _validate_run_identity(run, run_id, attempt)
    workflow_name = REVIEWER_WORKFLOW_NAMES[repo]
    if run.get("name") != workflow_name or run.get("event") != "workflow_dispatch":
        raise ResumeError("reviewer run origin is not trusted")
    if run.get("head_branch") != "main":
        raise ResumeError("reviewer run did not execute from reviewer main")
    run_package = reviewer_package_from_title(run.get("display_title"), workflow_name, actor)
    if run_package != package_id:
        raise ResumeError("reviewer run title/package binding failed")
    conclusion = run.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise ResumeError("reviewer conclusion is invalid")
    head_sha = run.get("head_sha")
    encoded = urllib.parse.quote("requests/current.json", safe="/")
    request_payload = api_json(reviewer_token, api, f"/contents/{encoded}?ref={head_sha}")
    request = _decode_content(request_payload)
    if request.get("package_id") != package_id:
        raise ResumeError("reviewer run HEAD does not contain the callback package")
    expected_head = request.get("expected_head")
    if not isinstance(expected_head, str) or SHA_RE.fullmatch(expected_head) is None:
        raise ResumeError("reviewer request expected_head is invalid")
    return {
        "actor": actor,
        "repo": repo,
        "run_id": run_id,
        "run_attempt": attempt,
        "package_id": package_id,
        "parent_architect_run_id": parent_run_id,
        "source_main_sha": expected_head,
        "run_head_sha": head_sha,
        "conclusion": conclusion,
        # Reviewer provider usage is intentionally not inferred here. The bridge exposes
        # completion, while Sol re-adjudicates semantics from repository evidence.
        "agent_cost_usd": Decimal("0"),
        "agent_cost_kind": "provider_usage_not_in_orchestrator",
    }


def architect_archive(token: str, run_id: int) -> bytes:
    run = api_json(token, ORCH_API, f"/actions/runs/{run_id}")
    if not isinstance(run, dict):
        raise ResumeError("parent architect run payload is invalid")
    if run.get("name") != "QORE Architect autonomous V2" or run.get("event") != "workflow_dispatch":
        raise ResumeError("parent architect run is not Autonomous V2 workflow_dispatch")
    if run.get("status") != "completed":
        raise ResumeError("parent architect run has not completed")
    return artifact_bytes(token, ORCH_REPO, run_id, f"qore-architect-v2-{run_id}")


def architect_cost(archive: bytes) -> tuple[Decimal, list[str], int]:
    usages: list[dict[str, Any]] = []
    response_ids: set[str] = set()
    cost = Decimal("0")
    notes: list[str] = []
    for name in ("sol-usage-initial.json", "sol-usage.json"):
        usage = extract_json(archive, name, required=False)
        if usage is None:
            continue
        response_id = usage.get("response_id")
        if isinstance(response_id, str) and response_id in response_ids:
            continue
        if isinstance(response_id, str):
            response_ids.add(response_id)
        usages.append(usage)
        cost += estimate_usage_cost(usage)
    if not usages:
        raise ResumeError("parent architect artifact lacks Sol usage evidence")
    sol_calls = len(usages)
    reconstruction = extract_json(archive, "architect-decision-before-reconstruction.json", required=False)
    if reconstruction is not None and len(usages) < MAX_SOL_CALLS_PER_ARCHITECT_RUN:
        # The current V2 workflow may overwrite an intermediate usage file during its
        # bounded reconstruction pass. Reserve one possible paid pass rather than
        # undercounting a call that may already have been spent.
        cost += UNKNOWN_SOL_PASS_RESERVE_USD
        sol_calls += 1
        notes.append("reserved_one_possible_overwritten_sol_pass")
    if sol_calls > MAX_SOL_CALLS_PER_ARCHITECT_RUN:
        raise ResumeError("parent architect exceeded the bounded Sol-call contract")
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_UP), notes, sol_calls


def parent_package_binding(archive: bytes, completion: dict[str, Any]) -> None:
    actor = completion["actor"]
    if actor == "CODEX":
        request = extract_json(archive, "codex-engineering-request.json")
        if request is None or request.get("package_id") != completion["package_id"]:
            raise ResumeError("parent architect artifact does not bind exact Codex package")
        if request.get("source_main_sha") != completion["source_main_sha"]:
            raise ResumeError("parent architect/Codex source SHA binding failed")
        return
    package = extract_json(archive, "reviewer-package.json")
    if package is None:
        raise ResumeError("parent architect artifact lacks reviewer package metadata")
    if package.get("package_id") != completion["package_id"] or package.get("target_repo") != completion["repo"]:
        raise ResumeError("parent architect/reviewer package binding failed")
    if package.get("head") != completion["source_main_sha"]:
        raise ResumeError("parent architect/reviewer HEAD binding failed")


def _receipt_for_run(token: str, run_id: int) -> dict[str, Any] | None:
    try:
        archive = artifact_bytes(token, ORCH_REPO, run_id, f"qore-agent-resume-{run_id}")
    except ResumeError:
        return None
    return extract_json(archive, "qore-resume-receipt.json", required=False)


def recent_receipts(token: str) -> list[dict[str, Any]]:
    payload = api_json(
        token,
        ORCH_API,
        f"/actions/workflows/{RESUME_WORKFLOW}/runs?per_page={MAX_RECEIPT_SCAN}",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ResumeError("resume workflow history is invalid")
    receipts: list[dict[str, Any]] = []
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict) or run.get("status") != "completed" or type(run.get("id")) is not int:
            continue
        receipt = _receipt_for_run(token, run["id"])
        if isinstance(receipt, dict):
            receipts.append(receipt)
    return receipts


def lineage_for_parent(receipts: list[dict[str, Any]], parent_architect_run_id: int) -> dict[str, Any] | None:
    matches = [r for r in receipts if r.get("child_architect_run_id") == parent_architect_run_id and r.get("dispatched") is True]
    if len(matches) > 1:
        raise ResumeError("multiple resume receipts claim the same child architect run")
    return matches[0] if matches else None


def prior_event(receipts: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    matches = [r for r in receipts if r.get("event_key") == key]
    if len(matches) > 1:
        raise ResumeError("duplicate resume receipts exist for one completion event")
    return matches[0] if matches else None


def _decimal_from_receipt(value: Any) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # Decimal can raise several subclasses on malformed input.
        raise ResumeError("receipt cumulative cost is invalid") from exc
    if parsed < 0:
        raise ResumeError("receipt cumulative cost is negative")
    return parsed


def dispatch_architect(token: str) -> int:
    before_payload = api_json(token, ORCH_API, f"/actions/workflows/{ARCHITECT_WORKFLOW}/runs?event=workflow_dispatch&per_page=20")
    if not isinstance(before_payload, dict) or not isinstance(before_payload.get("workflow_runs"), list):
        raise ResumeError("could not snapshot architect workflow history before dispatch")
    before_ids = {r.get("id") for r in before_payload["workflow_runs"] if isinstance(r, dict) and type(r.get("id")) is int}
    status = api_status(
        token,
        ORCH_API,
        f"/actions/workflows/{ARCHITECT_WORKFLOW}/dispatches",
        method="POST",
        payload={
            "ref": "main",
            "inputs": {
                "confirm_api_spend": "true",
                "codex_worker_mode": "execute",
                "external_dispatch_mode": "execute",
                "sol_reasoning_effort": "auto",
            },
        },
    )
    if status != 204:
        raise ResumeError(f"Autonomous V2 resume dispatch failed with HTTP {status}")
    for _attempt in range(20):
        time.sleep(2)
        payload = api_json(token, ORCH_API, f"/actions/workflows/{ARCHITECT_WORKFLOW}/runs?event=workflow_dispatch&per_page=20")
        if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
            continue
        new_runs = [
            r
            for r in payload["workflow_runs"]
            if isinstance(r, dict)
            and type(r.get("id")) is int
            and r.get("id") not in before_ids
            and r.get("head_branch") == "main"
            and r.get("status") in {"queued", "in_progress", "completed"}
        ]
        if len(new_runs) == 1:
            return new_runs[0]["id"]
        if len(new_runs) > 1:
            raise ResumeError("architect resume dispatch is ambiguous: multiple new runs observed")
    raise ResumeError("Autonomous V2 dispatch returned 204 but no exact new run was observed")


def build_receipt(
    completion: dict[str, Any],
    receipts: list[dict[str, Any]],
    architect_cost_usd: Decimal,
    architect_cost_notes: list[str],
    *,
    mode: str,
    max_auto_resumes: int,
    max_spend: Decimal,
    architect_sol_calls: int = 1,
    max_sol_calls: int = DEFAULT_MAX_SOL_CALLS,
    max_codex_jobs: int = DEFAULT_MAX_CODEX_JOBS,
) -> dict[str, Any]:
    key = event_key(completion["actor"], completion["repo"], completion["run_id"], completion["run_attempt"])
    duplicate = prior_event(receipts, key)
    if duplicate is not None:
        return {
            "schema_version": "qore.orchestration.resume.receipt.v1",
            "event_key": key,
            "actor": completion["actor"],
            "package_id": completion["package_id"],
            "parent_architect_run_id": completion["parent_architect_run_id"],
            "session_id": duplicate.get("session_id"),
            "cycle_index": duplicate.get("cycle_index"),
            "estimated_spend_usd": duplicate.get("estimated_spend_usd"),
            "sol_calls_used": duplicate.get("sol_calls_used"),
            "codex_jobs_used": duplicate.get("codex_jobs_used"),
            "dispatched": False,
            "child_architect_run_id": None,
            "stop_reason": "EXACT_COMPLETION_EVENT_ALREADY_RECEIPTED",
            "production_authority": False,
        }

    if architect_sol_calls < 1 or architect_sol_calls > MAX_SOL_CALLS_PER_ARCHITECT_RUN:
        raise ResumeError("architect Sol-call evidence violates bounded-run contract")

    prior = lineage_for_parent(receipts, completion["parent_architect_run_id"])
    if prior is None:
        session_id = f"QORE-ORCH-R{completion['parent_architect_run_id']}"
        cycle_index = 0
        cumulative = Decimal("0")
        package_history: list[str] = []
        prior_sol_calls = 0
        prior_codex_jobs = 0
    else:
        session_id = str(prior.get("session_id") or "")
        if not re.fullmatch(r"QORE-ORCH-R[1-9][0-9]*", session_id):
            raise ResumeError("prior receipt session_id is invalid")
        cycle_index = _nonnegative_int(prior.get("cycle_index"), "prior cycle_index")
        cumulative = _decimal_from_receipt(prior.get("estimated_spend_usd"))
        history_raw = prior.get("package_history", [])
        if not isinstance(history_raw, list) or not all(isinstance(item, str) for item in history_raw):
            raise ResumeError("prior package history is invalid")
        package_history = list(history_raw)
        prior_sol_calls = _nonnegative_int(prior.get("sol_calls_used"), "prior sol_calls_used")
        prior_codex_jobs = _nonnegative_int(prior.get("codex_jobs_used"), "prior codex_jobs_used")

    if completion["package_id"] in package_history:
        stop_reason = "LOOP_SIGNATURE_REPEATED_PACKAGE"
    else:
        stop_reason = ""
    package_history.append(completion["package_id"])

    total = cumulative + architect_cost_usd + completion["agent_cost_usd"]
    total = total.quantize(Decimal("0.000001"), rounding=ROUND_UP)
    sol_calls_used = prior_sol_calls + architect_sol_calls
    codex_jobs_used = prior_codex_jobs + (1 if completion["actor"] == "CODEX" else 0)
    next_cycle = cycle_index + 1
    if not stop_reason and next_cycle > max_auto_resumes:
        stop_reason = "AUTO_RESUME_CYCLE_CAP_REACHED"
    if not stop_reason and total >= max_spend:
        stop_reason = "ESTIMATED_SPEND_CAP_REACHED"
    if not stop_reason and sol_calls_used > max_sol_calls:
        stop_reason = "SOL_CALL_CAP_EXCEEDED"
    if not stop_reason and sol_calls_used + MAX_SOL_CALLS_PER_ARCHITECT_RUN > max_sol_calls:
        stop_reason = "SOL_CALL_CAP_REACHED"
    if not stop_reason and codex_jobs_used >= max_codex_jobs:
        stop_reason = "CODEX_JOB_CAP_REACHED"
    if mode != "execute" and not stop_reason:
        stop_reason = "DRY_RUN_ONLY"

    return {
        "schema_version": "qore.orchestration.resume.receipt.v1",
        "event_key": key,
        "actor": completion["actor"],
        "repository": completion["repo"],
        "source_run_id": completion["run_id"],
        "source_run_attempt": completion["run_attempt"],
        "source_conclusion": completion.get("conclusion"),
        "package_id": completion["package_id"],
        "parent_architect_run_id": completion["parent_architect_run_id"],
        "session_id": session_id,
        "cycle_index": next_cycle,
        "max_auto_resumes": max_auto_resumes,
        "estimated_spend_usd": str(total),
        "max_estimated_spend_usd": str(max_spend),
        "architect_cost_usd": str(architect_cost_usd),
        "architect_cost_notes": architect_cost_notes,
        "agent_cost_usd": str(completion["agent_cost_usd"]),
        "agent_cost_kind": completion["agent_cost_kind"],
        "sol_calls_used": sol_calls_used,
        "max_sol_calls": max_sol_calls,
        "sol_calls_reserved_per_architect_run": MAX_SOL_CALLS_PER_ARCHITECT_RUN,
        "codex_jobs_used": codex_jobs_used,
        "max_codex_jobs": max_codex_jobs,
        "package_history": package_history,
        "dispatched": False,
        "child_architect_run_id": None,
        "stop_reason": stop_reason or None,
        "production_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--mode", choices=["dry_run", "execute"], required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-auto-resumes", type=int, default=DEFAULT_MAX_AUTO_RESUMES)
    parser.add_argument("--max-estimated-spend-usd", default=str(DEFAULT_MAX_ESTIMATED_SPEND_USD))
    parser.add_argument("--max-sol-calls", type=int, default=DEFAULT_MAX_SOL_CALLS)
    parser.add_argument("--max-codex-jobs", type=int, default=DEFAULT_MAX_CODEX_JOBS)
    args = parser.parse_args()
    if args.max_auto_resumes < 1 or args.max_auto_resumes > 12:
        raise ResumeError("max-auto-resumes must be between 1 and 12")
    try:
        max_spend = Decimal(args.max_estimated_spend_usd)
    except Exception as exc:
        raise ResumeError("max-estimated-spend-usd is invalid") from exc
    if max_spend <= 0 or max_spend > Decimal("25.00"):
        raise ResumeError("max-estimated-spend-usd must be > 0 and <= 25")
    if args.max_sol_calls < MAX_SOL_CALLS_PER_ARCHITECT_RUN or args.max_sol_calls > 36:
        raise ResumeError("max-sol-calls must be between 3 and 36")
    if args.max_codex_jobs < 1 or args.max_codex_jobs > 12:
        raise ResumeError("max-codex-jobs must be between 1 and 12")

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ResumeError("GITHUB_TOKEN is required")
    event = json.loads(Path(args.event).read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise ResumeError("GitHub event payload is not an object")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_run":
        completion = parse_codex_event(event, token)
    elif event_name == "repository_dispatch":
        reviewer_token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
        if not reviewer_token:
            raise ResumeError("QORE_REVIEWER_DISPATCH_TOKEN is required for reviewer callback verification")
        completion = parse_reviewer_event(event, reviewer_token)
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
        raise ResumeError(f"unsupported completion event: {event_name or '<missing>'}")

    archive = architect_archive(token, completion["parent_architect_run_id"])
    parent_package_binding(archive, completion)
    sol_cost, sol_notes, sol_calls = architect_cost(archive)
    receipts = recent_receipts(token)
    receipt = build_receipt(
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
        child_run_id = dispatch_architect(token)
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
    except ResumeError as exc:
        print(f"AGENT_RESUME_ERROR: {exc}")
        raise SystemExit(21) from exc
