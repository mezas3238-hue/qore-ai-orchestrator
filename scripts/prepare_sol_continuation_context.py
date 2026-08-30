#!/usr/bin/env python3
"""Attach the prior non-terminal architect decision to a bounded Sol context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_PRIOR_DECISION_CHARS = 16000
MAX_ARCHITECT_CONTEXT_CHARS = 190000


def compact_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def prepare(context: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    dynamic = context.get("dynamic_context")
    if not isinstance(dynamic, dict):
        raise ValueError("model context lacks dynamic_context")
    if decision.get("status") != "RECONSTRUCTION_REQUIRED":
        raise ValueError("continuation context requires RECONSTRUCTION_REQUIRED")

    rendered = json.dumps(decision, separators=(",", ":"), ensure_ascii=False)
    if len(rendered) > MAX_PRIOR_DECISION_CHARS:
        raise ValueError("prior architect decision exceeds continuation bound")

    dynamic["controller_continuation"] = {
        "kind": "RECONSTRUCTION_CONTINUATION",
        "instruction": (
            "The prior architect step was non-terminal. Re-evaluate the refreshed GitHub evidence. "
            "Do not stop at synthesis. Route actionable work to an agent, use WAITING_AGENT only "
            "for an exact observed queued/in-progress agent job, use PROGRAM_COMPLETE only if the "
            "roadmap is actually complete, or request another reconstruction only for concrete still-missing evidence."
        ),
        "prior_decision": decision,
    }
    chars = compact_chars(
        {
            "stable_context": context.get("stable_context"),
            "dynamic_context": dynamic,
        }
    )
    if chars > MAX_ARCHITECT_CONTEXT_CHARS:
        raise ValueError(
            f"continuation architect context exceeds bound: {chars} > {MAX_ARCHITECT_CONTEXT_CHARS}"
        )
    metrics = context.get("metrics")
    if isinstance(metrics, dict):
        metrics["architect_context_chars"] = chars
        metrics["continuation_context"] = True
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    if not isinstance(context, dict) or not isinstance(decision, dict):
        raise SystemExit("continuation inputs must be JSON objects")
    try:
        result = prepare(context, decision)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "SOL_CONTINUATION_CONTEXT chars={}".format(
            (result.get("metrics") or {}).get("architect_context_chars")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
