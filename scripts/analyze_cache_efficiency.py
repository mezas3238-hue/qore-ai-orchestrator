from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


def analyze_cache_efficiency(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sessions: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        session = str(event.get("session_id", "")).strip()
        actor = str(event.get("actor", "")).strip()
        model = str(event.get("model", "")).strip()
        if not session or not actor or not model:
            raise ValueError("cache event requires session_id, actor and model")
        sessions[(session, actor, model)].append(event)

    rows: list[dict[str, Any]] = []
    for (session, actor, model), group in sorted(sessions.items()):
        total_input = 0
        total_hits = 0
        total_writes = 0
        for event in group:
            input_tokens = event.get("input_tokens", 0)
            hits = event.get("cached_input_tokens", 0)
            writes = event.get("cache_write_tokens", 0)
            for name, value in (
                ("input_tokens", input_tokens),
                ("cached_input_tokens", hits),
                ("cache_write_tokens", writes),
            ):
                if type(value) is not int or value < 0:
                    raise ValueError(f"{name} must be a non-negative exact int")
            if hits > input_tokens:
                raise ValueError("cached input cannot exceed input tokens")
            total_input += input_tokens
            total_hits += hits
            total_writes += writes

        rows.append(
            {
                "session_id": session,
                "actor": actor,
                "model": model,
                "calls": len(group),
                "input_tokens": total_input,
                "cache_hit_tokens": total_hits,
                "cache_write_tokens": total_writes,
                "cache_hit_ratio": total_hits / total_input if total_input else 0.0,
                "write_to_hit_ratio": (
                    total_writes / total_hits if total_hits else None
                ),
                "cache_write_without_any_read_hit": total_writes > 0 and total_hits == 0,
            }
        )

    return {
        "schema_version": "qore.cache.efficiency.v1",
        "sessions": rows,
        "audit_rule": (
            "CACHE_HIT_RATIO_IS_DIAGNOSTIC_ONLY;HIGH_HIT_RATE_DOES_NOT_JUSTIFY_DUPLICATED_CONTEXT"
        ),
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit QORE prompt-cache economics deterministically.")
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    normalized = json.loads(args.normalized.read_text(encoding="utf-8"))
    result = analyze_cache_efficiency(normalized.get("events", []))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
