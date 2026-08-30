#!/usr/bin/env python3
"""Select the exact architect recovery controller from a GitHub push event."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PRE_SPEND_PATH = "recovery/architect-pre-spend-current.json"
POST_SPEND_PATH = "recovery/architect-post-spend-current.json"
MODES = {
    PRE_SPEND_PATH: "pre_spend",
    POST_SPEND_PATH: "post_spend",
}


class RoutingError(ValueError):
    pass


def changed_paths(event: dict[str, Any]) -> list[str]:
    head = event.get("head_commit")
    if not isinstance(head, dict):
        raise RoutingError("push event lacks head_commit")
    observed: set[str] = set()
    for field in ("added", "modified", "removed"):
        values = head.get(field)
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise RoutingError(f"push head_commit.{field} is invalid")
        observed.update(values)
    return sorted(observed)


def select_mode(event: dict[str, Any]) -> str:
    if event.get("ref") != "refs/heads/main":
        raise RoutingError("recovery push is not on main")
    paths = changed_paths(event)
    if len(paths) != 1:
        raise RoutingError("recovery activation must change exactly one path")
    mode = MODES.get(paths[0])
    if mode is None:
        raise RoutingError("recovery activation path is not allowlisted")
    return mode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    args = parser.parse_args()
    try:
        event = json.loads(Path(args.event).read_text(encoding="utf-8"))
        if not isinstance(event, dict):
            raise RoutingError("push event is not an object")
        print(select_mode(event))
    except (OSError, json.JSONDecodeError, RoutingError) as exc:
        raise SystemExit(f"RECOVERY_PUSH_ROUTING_ERROR: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
