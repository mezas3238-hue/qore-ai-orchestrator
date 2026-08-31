from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


NAVIGATION_TOOLS = frozenset(
    {"list_files", "read_file", "search_text", "reference_diff", "git_diff"}
)
PATCH_TOOLS = frozenset({"apply_patch"})
TEST_TOOLS = frozenset({"run_tests", "run_quality_gate"})


def analyze_turn_trace(payload: Mapping[str, Any]) -> dict[str, Any]:
    trace = payload.get("turn_trace", [])
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
        raise ValueError("turn_trace must be an array")

    tool_counts: Counter[str] = Counter()
    first_patch_turn: int | None = None
    first_test_turn: int | None = None
    pre_patch_input = 0
    pre_patch_cached = 0
    pre_patch_output = 0
    navigation_before_patch = 0

    for expected_turn, row in enumerate(trace, start=1):
        if not isinstance(row, Mapping):
            raise ValueError("turn_trace entries must be objects")
        turn = row.get("turn")
        if type(turn) is not int or turn != expected_turn:
            raise ValueError("turn_trace must use contiguous exact turn numbers")
        tool = str(row.get("tool", ""))
        tool_counts[tool] += 1
        if tool in PATCH_TOOLS and first_patch_turn is None:
            first_patch_turn = turn
        if tool in TEST_TOOLS and first_test_turn is None:
            first_test_turn = turn
        if first_patch_turn is None:
            input_tokens = row.get("input_tokens", 0)
            cached_tokens = row.get("cached_tokens", 0)
            output_tokens = row.get("output_tokens", 0)
            for name, value in (
                ("input_tokens", input_tokens),
                ("cached_tokens", cached_tokens),
                ("output_tokens", output_tokens),
            ):
                if type(value) is not int or value < 0:
                    raise ValueError(f"{name} must be a non-negative exact int")
            pre_patch_input += input_tokens
            pre_patch_cached += cached_tokens
            pre_patch_output += output_tokens
            if tool in NAVIGATION_TOOLS:
                navigation_before_patch += 1

    total_turns = len(trace)
    total_input = payload.get("input_tokens", 0)
    total_cached = payload.get("cached_tokens", 0)
    total_output = payload.get("output_tokens", 0)
    for name, value in (
        ("input_tokens", total_input),
        ("cached_tokens", total_cached),
        ("output_tokens", total_output),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative exact int")

    return {
        "schema_version": "qore.codex.turn.economics.v1",
        "worker_version": payload.get("worker_version"),
        "model": payload.get("model"),
        "turns": total_turns,
        "first_patch_turn": first_patch_turn,
        "first_test_turn": first_test_turn,
        "navigation_turns_before_first_patch": navigation_before_patch,
        "pre_patch_input_tokens": pre_patch_input,
        "pre_patch_cached_tokens": pre_patch_cached,
        "pre_patch_output_tokens": pre_patch_output,
        "total_input_tokens": total_input,
        "total_cached_tokens": total_cached,
        "total_output_tokens": total_output,
        "pre_patch_input_share": (pre_patch_input / total_input) if total_input else 0.0,
        "cache_ratio": (total_cached / total_input) if total_input else 0.0,
        "tool_counts": dict(sorted(tool_counts.items())),
        "materialized_reference_sha": payload.get("materialized_reference_sha"),
        "budget_tokens": payload.get("budget_tokens"),
        "max_total_tokens": payload.get("max_total_tokens"),
        "max_turns": payload.get("max_turns"),
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure Codex exploration cost without a model call.")
    parser.add_argument("--usage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = json.loads(args.usage.read_text(encoding="utf-8"))
    result = analyze_turn_trace(payload)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
