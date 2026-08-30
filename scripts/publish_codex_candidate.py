#!/usr/bin/env python3
"""Publish a verified local Codex candidate as a deterministic qore-core draft PR.

This controller is the only Codex path that receives QORE_CODEX_ENGINEER_TOKEN.
The model worker never receives this credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = "mezas3238-hue/qore-core"
API = f"https://api.github.com/repos/{REPO}"
USER_AGENT = "qore-ai-orchestrator/1.0"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class PublishError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api(
    token: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + path + query, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        return exc.code, body


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        raise PublishError(f"git {' '.join(args)} failed: {result.stdout[-4000:]}")
    return result.stdout


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug or "contract")[:55]


def _load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PublishError(f"{path} must contain an object")
    return value


def _validate(request: dict[str, Any], result: dict[str, Any], repo: Path) -> tuple[str, str, str]:
    if request.get("schema_version") != "qore.codex.engineering.request.v1":
        raise PublishError("unexpected engineering request schema")
    if result.get("schema_version") != "qore.codex.worker.result.v1":
        raise PublishError("unexpected worker result schema")
    source = request.get("source_main_sha")
    contract = request.get("engineering_contract")
    if not isinstance(source, str) or not SHA_RE.fullmatch(source):
        raise PublishError("source_main_sha is invalid")
    if not isinstance(contract, dict) or contract.get("target_repository") != REPO:
        raise PublishError("request target is not qore-core")
    contract_id = contract.get("contract_id")
    objective = contract.get("objective")
    if not isinstance(contract_id, str) or not contract_id:
        raise PublishError("contract_id is invalid")
    if not isinstance(objective, str) or not objective.strip():
        raise PublishError("contract objective is invalid")
    if result.get("source_main_sha") != source or result.get("contract_id") != contract_id:
        raise PublishError("worker result is not bound to the request")
    if result.get("status") != "READY" or result.get("quality_gate_success") is not True:
        raise PublishError("only a READY worker result with green Quality Gate can publish")
    if request.get("production_authority") is not False or result.get("production_authority") is not False:
        raise PublishError("Production authority invariant violated")
    if _git(repo, "rev-parse", "HEAD").strip() != source:
        raise PublishError("local repository HEAD no longer equals source_main_sha")
    changed = _git(repo, "status", "--porcelain").strip().splitlines()
    if not changed:
        raise PublishError("candidate working tree is empty")
    return source, contract_id, objective.strip()


def _deterministic_commit(repo: Path, source: str, contract_id: str) -> str:
    _git(repo, "add", "-A")
    if not _git(repo, "diff", "--cached", "--name-only").strip():
        raise PublishError("staged candidate is empty")
    source_epoch_raw = _git(repo, "show", "-s", "--format=%ct", source).strip()
    try:
        epoch = int(source_epoch_raw) + 1
    except ValueError as exc:
        raise PublishError("could not derive deterministic commit timestamp") from exc
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "QORE Codex Worker",
            "GIT_AUTHOR_EMAIL": "qore-codex-worker@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "QORE Codex Worker",
            "GIT_COMMITTER_EMAIL": "qore-codex-worker@users.noreply.github.com",
            "GIT_AUTHOR_DATE": f"{epoch} +0000",
            "GIT_COMMITTER_DATE": f"{epoch} +0000",
        }
    )
    _git(repo, "commit", "-m", f"Codex candidate: {contract_id}", env=env)
    candidate = _git(repo, "rev-parse", "HEAD").strip()
    if not SHA_RE.fullmatch(candidate):
        raise PublishError("candidate commit SHA is invalid")
    parents = _git(repo, "show", "-s", "--format=%P", candidate).strip().split()
    if parents != [source]:
        raise PublishError("candidate commit parent is not the exact source main")
    return candidate


def _push_branch(repo: Path, token: str, branch: str, candidate: str) -> None:
    status, payload = _api(token, "GET", f"/git/ref/heads/{urllib.parse.quote(branch, safe='')}")
    if status == 200:
        existing = ((payload or {}).get("object") or {}).get("sha") if isinstance(payload, dict) else None
        if existing != candidate:
            raise PublishError("deterministic branch already exists at a different commit; refusing force-push")
        return
    if status != 404:
        raise PublishError(f"branch lookup returned HTTP {status}")

    with tempfile.TemporaryDirectory(prefix="qore-askpass-") as tmp:
        askpass = Path(tmp) / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\ncase \"$1\" in\n  *Username*) printf '%s\\n' 'x-access-token' ;;\n  *Password*) printf '%s\\n' \"$QORE_CODEX_ENGINEER_TOKEN\" ;;\n  *) exit 1 ;;\nesac\n",
            encoding="utf-8",
        )
        askpass.chmod(0o700)
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_TERMINAL_PROMPT"] = "0"
        _git(repo, "push", "origin", f"{candidate}:refs/heads/{branch}", env=env)


def _ensure_pr(token: str, branch: str, candidate: str, source: str, contract_id: str, objective: str) -> dict[str, Any]:
    status, existing = _api(
        token,
        "GET",
        "/pulls",
        params={"state": "open", "head": f"mezas3238-hue:{branch}", "per_page": "10"},
    )
    if status != 200 or not isinstance(existing, list):
        raise PublishError(f"open-PR lookup returned HTTP {status}")
    if existing:
        if len(existing) != 1:
            raise PublishError("multiple open PRs exist for deterministic Codex branch")
        pr = existing[0]
        head_sha = ((pr.get("head") or {}).get("sha")) if isinstance(pr, dict) else None
        if head_sha != candidate:
            raise PublishError("existing PR head does not equal candidate commit")
        return pr

    body = (
        "## QORE Codex engineering candidate\n\n"
        f"- Contract: `{contract_id}`\n"
        f"- Exact source main: `{source}`\n"
        f"- Candidate commit: `{candidate}`\n"
        "- Produced by bounded GPT-5.3-Codex local worker.\n"
        "- Full QORE Quality Gate passed before publication.\n"
        "- External reviews are not inherited; this is a new candidate.\n"
        "- No Production or real-capital authority.\n\n"
        f"Objective: {objective}\n\n"
        f"<!-- QORE-CODEX-CONTRACT:{contract_id} SOURCE:{source} -->"
    )
    status, pr = _api(
        token,
        "POST",
        "/pulls",
        payload={
            "title": f"Codex: {objective[:160]}",
            "head": branch,
            "base": "main",
            "body": body,
            "draft": True,
        },
    )
    if status != 201 or not isinstance(pr, dict):
        raise PublishError(f"draft PR creation returned HTTP {status}")
    return pr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--worker-result", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    token = os.environ.get("QORE_CODEX_ENGINEER_TOKEN", "").strip()
    if not token:
        print("QORE_CODEX_ENGINEER_TOKEN is not configured.", file=sys.stderr)
        return 2
    repo = Path(args.repo_dir).resolve()
    request = _load(args.request)
    worker_result = _load(args.worker_result)
    source, contract_id, objective = _validate(request, worker_result, repo)
    candidate = _deterministic_commit(repo, source, contract_id)
    branch = f"agent/codex-{_slug(contract_id)}-{source[:10]}"
    _push_branch(repo, token, branch, candidate)
    pr = _ensure_pr(token, branch, candidate, source, contract_id, objective)
    number = pr.get("number")
    if type(number) is not int:
        raise PublishError("published PR number is invalid")
    payload = {
        "schema_version": "qore.codex.publication.v1",
        "repository": REPO,
        "contract_id": contract_id,
        "source_main_sha": source,
        "candidate_commit_sha": candidate,
        "branch": branch,
        "pull_request_number": number,
        "pull_request_url": pr.get("html_url"),
        "draft": bool(pr.get("draft")),
        "production_authority": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_CANDIDATE_PUBLISHED pr={number} branch={branch} candidate={candidate}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"CODEX_CANDIDATE_PUBLISH_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8) from exc
