#!/usr/bin/env python3
"""Select the exact architect recovery controller from canonical GitHub commit files."""

from __future__ import annotations

import argparse
import os

import resume_after_agent_completion as base

PRE_SPEND_PATH = "recovery/architect-pre-spend-current.json"
POST_SPEND_PATH = "recovery/architect-post-spend-current.json"
MODES = {
    PRE_SPEND_PATH: "pre_spend",
    POST_SPEND_PATH: "post_spend",
}


class RoutingError(ValueError):
    pass


def changed_paths_from_commit(token: str, sha: str) -> list[str]:
    if not isinstance(sha, str) or base.SHA_RE.fullmatch(sha) is None:
        raise RoutingError("recovery activation SHA is invalid")
    payload = base.api_json(token, base.ORCH_API, f"/commits/{sha}")
    if not isinstance(payload, dict) or payload.get("sha") != sha:
        raise RoutingError("recovery activation commit binding failed")
    files = payload.get("files")
    if not isinstance(files, list):
        raise RoutingError("recovery activation commit file list is invalid")
    observed: list[str] = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str) or not item["filename"]:
            raise RoutingError("recovery activation commit contains invalid file evidence")
        observed.append(item["filename"])
    return sorted(set(observed))


def select_mode(token: str, sha: str) -> str:
    paths = changed_paths_from_commit(token, sha)
    if len(paths) != 1:
        raise RoutingError("recovery activation must change exactly one path")
    mode = MODES.get(paths[0])
    if mode is None:
        raise RoutingError("recovery activation path is not allowlisted")
    return mode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("RECOVERY_PUSH_ROUTING_ERROR: GITHUB_TOKEN is not configured")
    try:
        print(select_mode(token, args.sha))
    except (base.ResumeError, RoutingError) as exc:
        raise SystemExit(f"RECOVERY_PUSH_ROUTING_ERROR: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
