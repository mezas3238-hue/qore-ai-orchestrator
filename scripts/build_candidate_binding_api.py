from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from economic_control_plane import CandidateIdentity

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = "qore-ai-orchestrator/1.0"


def _sha(name: str, value: Any) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an exact lowercase SHA-40")
    return value


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_json(token: str, repository: str, path: str) -> Any:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}{path}",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"GitHub evidence fetch failed for {path}: {type(exc).__name__}") from exc


def build_candidate_binding_api(
    *,
    token: str,
    repository: str,
    pr_number: int,
    expected_base: str | None = None,
    expected_head: str | None = None,
    expected_synthetic: str | None = None,
) -> dict[str, Any]:
    if not isinstance(repository, str) or not repository.strip():
        raise ValueError("repository must be non-empty")
    if type(pr_number) is not int or pr_number <= 0:
        raise ValueError("pr_number must be a positive exact int")

    pr = api_json(token, repository, f"/pulls/{pr_number}")
    if not isinstance(pr, Mapping):
        raise ValueError("pull request evidence is invalid")
    if pr.get("state") != "open" or pr.get("merged") is not False:
        raise ValueError("candidate pull request must remain open and unmerged")
    base_raw = pr.get("base")
    head_raw = pr.get("head")
    if not isinstance(base_raw, Mapping) or not isinstance(head_raw, Mapping):
        raise ValueError("pull request base/head evidence is invalid")

    base = _sha("base_sha", base_raw.get("sha"))
    head = _sha("head_sha", head_raw.get("sha"))
    synthetic = _sha("synthetic_sha", pr.get("merge_commit_sha"))
    if expected_base is not None and base != _sha("expected_base", expected_base):
        raise ValueError("live BASE no longer matches expected BASE")
    if expected_head is not None and head != _sha("expected_head", expected_head):
        raise ValueError("live HEAD no longer matches expected HEAD")
    if expected_synthetic is not None and synthetic != _sha("expected_synthetic", expected_synthetic):
        raise ValueError("live SYNTHETIC no longer matches expected SYNTHETIC")

    head_commit = api_json(token, repository, f"/git/commits/{head}")
    synthetic_commit = api_json(token, repository, f"/git/commits/{synthetic}")
    if not isinstance(head_commit, Mapping) or not isinstance(synthetic_commit, Mapping):
        raise ValueError("candidate commit evidence is invalid")
    head_tree = _sha("head_tree", (head_commit.get("tree") or {}).get("sha") if isinstance(head_commit.get("tree"), Mapping) else None)
    synthetic_tree = _sha(
        "synthetic_tree",
        (synthetic_commit.get("tree") or {}).get("sha") if isinstance(synthetic_commit.get("tree"), Mapping) else None,
    )
    parents_raw = synthetic_commit.get("parents")
    if not isinstance(parents_raw, list):
        raise ValueError("synthetic parent evidence is invalid")
    parents = tuple(
        _sha("synthetic_parent", item.get("sha"))
        for item in parents_raw
        if isinstance(item, Mapping)
    )
    if parents != (base, head):
        raise ValueError("SYNTHETIC parents must be exactly BASE then HEAD")
    if synthetic_tree != head_tree:
        raise ValueError("SYNTHETIC tree must equal HEAD tree")

    compare = api_json(
        token,
        repository,
        f"/compare/{urllib.parse.quote(base, safe='')}...{urllib.parse.quote(head, safe='')}",
    )
    if not isinstance(compare, Mapping):
        raise ValueError("BASE..HEAD compare evidence is invalid")
    if compare.get("status") not in {"ahead", "identical"}:
        raise ValueError("BASE is not an ancestor of HEAD")

    candidate = CandidateIdentity(
        repository=repository,
        base_sha=base,
        head_sha=head,
        tree_sha=head_tree,
        synthetic_sha=synthetic,
        production_authority=False,
    )
    return {
        "schema_version": "qore.candidate.binding.api.v1",
        "candidate_id": candidate.candidate_id,
        "repository": repository,
        "pull_request_number": pr_number,
        "base_sha": base,
        "head_sha": head,
        "tree_sha": head_tree,
        "synthetic_sha": synthetic,
        "synthetic_tree_sha": synthetic_tree,
        "synthetic_parents": list(parents),
        "base_is_ancestor_of_head": True,
        "draft": bool(pr.get("draft")),
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact candidate binding from GitHub API evidence.")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--expected-base")
    parser.add_argument("--expected-head")
    parser.add_argument("--expected-synthetic")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_candidate_binding_api(
        token=os.environ.get("GITHUB_TOKEN", "").strip(),
        repository=args.repository,
        pr_number=args.pr_number,
        expected_base=args.expected_base,
        expected_head=args.expected_head,
        expected_synthetic=args.expected_synthetic,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
