#!/usr/bin/env python3
"""Add bounded reviewer control-plane evidence to the Codex engineering context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_ENGINEER_CONTEXT_CHARS = 70000


def compact_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def augment(context: dict[str, Any]) -> dict[str, Any]:
    dynamic = context.get("dynamic_context")
    engineer = context.get("engineer_context")
    if not isinstance(dynamic, dict) or not isinstance(engineer, dict):
        raise ValueError("model context lacks dynamic/engineer sections")
    external = dynamic.get("external_reviewer_state")
    if isinstance(external, dict):
        engineer["external_reviewer_state"] = external
    chars = compact_chars(engineer)
    if chars > MAX_ENGINEER_CONTEXT_CHARS:
        raise ValueError(
            f"engineer context exceeds bound after reviewer evidence: {chars} > {MAX_ENGINEER_CONTEXT_CHARS}"
        )
    metrics = context.get("metrics")
    if isinstance(metrics, dict):
        metrics["engineer_context_chars"] = chars
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise SystemExit("model context must be an object")
    try:
        augmented = augment(context)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = Path(args.output)
    output.write_text(json.dumps(augmented, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "ENGINEER_CONTEXT_AUGMENTED chars={}".format(
            (augmented.get("metrics") or {}).get("engineer_context_chars")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
