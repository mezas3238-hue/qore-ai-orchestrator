#!/usr/bin/env python3
"""Expose bounded Codex worker state in the model-facing architect context."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_ARCHITECT_CONTEXT_CHARS = 190_000


def compact_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if not isinstance(context, dict) or context.get("schema_version") != "qore.model.context.v1":
        raise SystemExit("unexpected model context")
    dynamic = context.get("dynamic_context")
    stable = context.get("stable_context")
    if not isinstance(dynamic, dict) or not isinstance(stable, dict):
        raise SystemExit("model context sections are missing")
    state = snapshot.get("codex_worker_state")
    if state is not None and not isinstance(state, dict):
        raise SystemExit("codex_worker_state is invalid")
    dynamic["codex_worker_state"] = state or {
        "schema_version": "qore.codex.worker.state.v1",
        "active_runs": [],
        "latest_completed": None,
        "recent_runs": [],
    }
    chars = compact_chars({"stable_context": stable, "dynamic_context": dynamic})
    if chars > MAX_ARCHITECT_CONTEXT_CHARS:
        raise SystemExit(f"architect context with Codex worker state exceeds bound: {chars}")
    metrics = context.setdefault("metrics", {})
    if isinstance(metrics, dict):
        metrics["architect_context_chars"] = chars
        metrics["codex_worker_state_included"] = True
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_WORKER_CONTEXT_OK architect_chars={chars}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
