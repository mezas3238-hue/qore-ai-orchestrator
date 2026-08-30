#!/usr/bin/env python3
"""Dispatch a reviewer package while safely superseding one terminal failed equivalent request."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_REPO = "mezas3238-hue/qore-claude-reviewer"
DEEPSEEK_REPO = "mezas3238-hue/qore-deepseek-reviewer"
QORE_REPO = "mezas3238-hue/qore-core"
ALLOWED_REPOS = {CLAUDE_REPO, DEEPSEEK_REPO}
WORKFLOW_CONTRACTS = {
    CLAUDE_REPO: ("Claude QORE review", ".github/workflows/claude-qore-review.yml"),
    DEEPSEEK_REPO: ("DeepSeek QORE review", ".github/workflows/deepseek-qore-review.yml"),
}
RETRYABLE_TERMINAL_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "startup_failure",
    "action_required",
    "stale",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = "qore-ai-orchestrator/1.0"


class RecoveryDispatchError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, headers=_headers(token), data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        raise RecoveryDispatchError(f"GitHub request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RecoveryDispatchError(f"GitHub request failed: {type(exc).__name__}") from exc


def content_url(repo: str, path: str) -> str:
    return f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}"


def get_content(repo: str, path: str, token: str, *, ref: str = "main", allow_404: bool = False) -> Any:
    return request_json(content_url(repo, path) + "?ref=" + urllib.parse.quote(ref, safe=""), token, allow_404=allow_404)


def put_content(
    repo: str,
    path: str,
    token: str,
    *,
    content: str,
    message: str,
    sha: str | None = None,
) -> Any:
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha is not None:
        payload["sha"] = sha
    return request_json(content_url(repo, path), token, method="PUT", payload=payload)


def decode_json_content(payload: Any, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
        raise RecoveryDispatchError(f"{label} content payload is invalid")
    try:
        encoded = "".join(payload["content"].split())
        value = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryDispatchError(f"{label} is invalid JSON/base64") from exc
    if not isinstance(value, dict):
        raise RecoveryDispatchError(f"{label} is not an object")
    return value


def equivalent_request(repo: str, prior: dict[str, Any], candidate: dict[str, Any]) -> bool:
    common = (
        prior.get("pr_number") == candidate.get("pr_number")
        and prior.get("expected_head") == candidate.get("expected_head")
        and prior.get("expected_synthetic") == candidate.get("expected_synthetic")
    )
    if not common:
        return False
    if repo == CLAUDE_REPO:
        return True
    if repo == DEEPSEEK_REPO:
        return prior.get("review_mode") == candidate.get("review_mode")
    return False


def _run_request_at_head(repo: str, run: dict[str, Any], token: str) -> dict[str, Any] | None:
    head = run.get("head_sha")
    if not isinstance(head, str) or SHA_RE.fullmatch(head) is None:
        return None
    payload = get_content(repo, "requests/current.json", token, ref=head, allow_404=True)
    if payload is None:
        return None
    try:
        return decode_json_content(payload, f"{repo}@{head}:requests/current.json")
    except RecoveryDispatchError:
        return None


def bound_runs_for_package(repo: str, package_id: str, token: str) -> list[dict[str, Any]]:
    workflow_name, workflow_path = WORKFLOW_CONTRACTS[repo]
    url = f"https://api.github.com/repos/{repo}/actions/runs?event=workflow_dispatch&per_page=100"
    payload = request_json(url, token)
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise RecoveryDispatchError("reviewer workflow history is invalid")
    title = f"{workflow_name} · {package_id}"
    matches: list[dict[str, Any]] = []
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict):
            continue
        if run.get("name") != workflow_name or run.get("path") != workflow_path or run.get("event") != "workflow_dispatch":
            continue
        if run.get("display_title") == title:
            matches.append(run)
            continue
        legacy_request = _run_request_at_head(repo, run, token)
        if isinstance(legacy_request, dict) and legacy_request.get("package_id") == package_id:
            matches.append(run)
    return matches


def semantic_publication_exists(pr_number: int, package_id: str, head: str, token: str) -> bool:
    if pr_number <= 0 or SHA_RE.fullmatch(head) is None:
        raise RecoveryDispatchError("semantic publication lookup identity is invalid")
    endpoints = (
        f"https://api.github.com/repos/{QORE_REPO}/pulls/{pr_number}/reviews?per_page=100",
        f"https://api.github.com/repos/{QORE_REPO}/issues/{pr_number}/comments?per_page=100",
        f"https://api.github.com/repos/{QORE_REPO}/pulls/{pr_number}/comments?per_page=100",
    )
    for url in endpoints:
        payload = request_json(url, token)
        if not isinstance(payload, list):
            raise RecoveryDispatchError("qore-core publication evidence is invalid")
        for item in payload:
            if not isinstance(item, dict):
                continue
            body = str(item.get("body") or "")
            commit_id = item.get("commit_id")
            if package_id in body and (commit_id in {None, head} or head in body):
                return True
    return False


def terminal_failed_equivalent_is_retryable(
    repo: str,
    prior: dict[str, Any],
    candidate: dict[str, Any],
    token: str,
) -> tuple[bool, dict[str, Any] | None]:
    prior_package = prior.get("package_id")
    candidate_package = candidate.get("package_id")
    if not isinstance(prior_package, str) or not prior_package:
        return False, None
    if prior_package == candidate_package:
        return False, None
    if not equivalent_request(repo, prior, candidate):
        return False, None
    runs = bound_runs_for_package(repo, prior_package, token)
    if len(runs) != 1:
        return False, None
    run = runs[0]
    if run.get("status") != "completed" or run.get("conclusion") not in RETRYABLE_TERMINAL_CONCLUSIONS:
        return False, run
    pr_number = candidate.get("pr_number")
    head = candidate.get("expected_head")
    if type(pr_number) is not int or not isinstance(head, str):
        raise RecoveryDispatchError("candidate PR/head identity is invalid")
    if semantic_publication_exists(pr_number, prior_package, head, token):
        return False, run
    return True, run


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--result-output", required=True)
    args = parser.parse_args()

    token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
    if not token:
        raise SystemExit("QORE_REVIEWER_DISPATCH_TOKEN is not configured")

    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    request_text = Path(args.request).read_text(encoding="utf-8")
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
    request_payload = json.loads(request_text)
    if not isinstance(metadata, dict) or not isinstance(request_payload, dict):
        raise SystemExit("recovery reviewer metadata/request must be objects")

    repo = metadata.get("target_repo")
    prompt_path = metadata.get("prompt_path")
    package_id = metadata.get("package_id")
    if repo not in ALLOWED_REPOS:
        raise SystemExit("recovery reviewer target repository is not allowlisted")
    if not isinstance(prompt_path, str) or not prompt_path.startswith("prompts/orchestrator/"):
        raise SystemExit("recovery reviewer prompt path is invalid")
    if not isinstance(package_id, str) or not package_id or request_payload.get("package_id") != package_id:
        raise SystemExit("recovery reviewer package binding is invalid")

    existing_prompt = get_content(repo, prompt_path, token, allow_404=True)
    if existing_prompt is not None:
        raise SystemExit("refusing duplicate recovery reviewer prompt/package path")
    current = get_content(repo, "requests/current.json", token)
    if not isinstance(current, dict) or not isinstance(current.get("sha"), str):
        raise SystemExit("could not bind reviewer requests/current.json blob SHA")
    prior = decode_json_content(current, "reviewer requests/current.json")
    if prior.get("package_id") == package_id:
        raise SystemExit("refusing duplicate recovery reviewer package")

    superseded: dict[str, Any] | None = None
    if equivalent_request(repo, prior, request_payload):
        try:
            retryable, prior_run = terminal_failed_equivalent_is_retryable(repo, prior, request_payload, token)
        except RecoveryDispatchError as exc:
            raise SystemExit(f"RECOVERY_REVIEW_DISPATCH_BLOCKED: {exc}") from exc
        if not retryable or prior_run is None:
            raise SystemExit("RECOVERY_REVIEW_DISPATCH_BLOCKED: equivalent prior reviewer stage is not safely supersedable")
        superseded = {
            "package_id": prior.get("package_id"),
            "run_id": prior_run.get("id"),
            "run_attempt": prior_run.get("run_attempt", 1),
            "conclusion": prior_run.get("conclusion"),
            "head_sha": prior_run.get("head_sha"),
        }

    try:
        prompt_commit = put_content(
            repo,
            prompt_path,
            token,
            content=prompt_text,
            message=f"Add {package_id} orchestrator recovery review prompt",
        )
        request_commit = put_content(
            repo,
            "requests/current.json",
            token,
            content=request_text,
            message=f"Recover terminal failed reviewer stage with {package_id}",
            sha=current["sha"],
        )
    except RecoveryDispatchError as exc:
        raise SystemExit(f"RECOVERY_REVIEW_DISPATCH_BLOCKED: {exc}") from exc

    result = {
        "schema_version": "qore.reviewer.dispatch.recovery.result.v1",
        "target_repo": repo,
        "package_id": package_id,
        "prompt_path": prompt_path,
        "prompt_commit": ((prompt_commit or {}).get("commit") or {}).get("sha"),
        "request_commit": ((request_commit or {}).get("commit") or {}).get("sha"),
        "superseded_terminal_request": superseded,
        "dispatch_trigger": "push requests/current.json -> existing reviewer auto-dispatch",
    }
    Path(args.result_output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"RECOVERY_REVIEW_DISPATCH_OK repo={repo} package={package_id} "
        f"superseded={None if superseded is None else superseded['package_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
