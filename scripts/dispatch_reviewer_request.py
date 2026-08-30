#!/usr/bin/env python3
"""Write an orchestrator-built prompt/request into an existing reviewer repository."""

from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_REPO = "mezas3238-hue/qore-claude-reviewer"
DEEPSEEK_REPO = "mezas3238-hue/qore-deepseek-reviewer"
ALLOWED_REPOS = {CLAUDE_REPO, DEEPSEEK_REPO}
USER_AGENT = "qore-ai-orchestrator/1.0"


class DispatchError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request_json(
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
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if allow_404 and exc.code == 404:
            return None
        raise DispatchError(f"GitHub reviewer write failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DispatchError(f"GitHub reviewer write failed: {type(exc).__name__}") from exc


def _content_url(repo: str, path: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="/")
    return f"https://api.github.com/repos/{repo}/contents/{encoded_path}"


def _get_content(repo: str, path: str, token: str, *, allow_404: bool = False) -> Any:
    return _request_json(_content_url(repo, path) + "?ref=main", token, allow_404=allow_404)


def _put_content(
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
    return _request_json(_content_url(repo, path), token, method="PUT", payload=payload)


def equivalent_request(repo: str, prior: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Return True when requests/current.json already represents the same review stage."""
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

    repo = metadata.get("target_repo")
    prompt_path = metadata.get("prompt_path")
    package_id = metadata.get("package_id")
    if repo not in ALLOWED_REPOS:
        raise SystemExit("reviewer target repository is not allowlisted")
    if not isinstance(prompt_path, str) or not prompt_path.startswith("prompts/orchestrator/"):
        raise SystemExit("reviewer prompt path is not an orchestrator path")
    if not isinstance(package_id, str) or not package_id:
        raise SystemExit("reviewer package ID is invalid")
    if request_payload.get("package_id") != package_id:
        raise SystemExit("request package ID does not match metadata")

    existing_prompt = _get_content(repo, prompt_path, token, allow_404=True)
    if existing_prompt is not None:
        raise SystemExit("refusing duplicate reviewer prompt/package path")

    current = _get_content(repo, "requests/current.json", token)
    if not isinstance(current, dict) or not isinstance(current.get("sha"), str):
        raise SystemExit("could not bind reviewer requests/current.json blob SHA")
    current_content = current.get("content")
    prior: dict[str, Any] = {}
    if isinstance(current_content, str):
        try:
            decoded = base64.b64decode(current_content).decode("utf-8")
            loaded = json.loads(decoded)
            prior = loaded if isinstance(loaded, dict) else {}
        except (ValueError, UnicodeError, json.JSONDecodeError):
            prior = {}

    if prior.get("package_id") == package_id:
        raise SystemExit("refusing duplicate reviewer package dispatch")
    if equivalent_request(repo, prior, request_payload):
        raise SystemExit("refusing equivalent reviewer stage already present in requests/current.json")

    prompt_commit = _put_content(
        repo,
        prompt_path,
        token,
        content=prompt_text,
        message=f"Add {package_id} orchestrator review prompt",
    )
    request_commit = _put_content(
        repo,
        "requests/current.json",
        token,
        content=request_text,
        message=f"Dispatch {package_id} from Sol orchestrator",
        sha=current["sha"],
    )

    result = {
        "target_repo": repo,
        "package_id": package_id,
        "prompt_path": prompt_path,
        "prompt_commit": ((prompt_commit or {}).get("commit") or {}).get("sha"),
        "request_commit": ((request_commit or {}).get("commit") or {}).get("sha"),
        "dispatch_trigger": "push requests/current.json -> existing reviewer auto-dispatch",
    }
    Path(args.result_output).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"REVIEW_DISPATCH_OK repo={repo} package={package_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
