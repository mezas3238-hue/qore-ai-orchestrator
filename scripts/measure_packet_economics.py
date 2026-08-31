from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


def compact_chars(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def estimate_tokens_from_chars(chars: int, *, chars_per_token: float) -> int:
    if type(chars) is not int or chars < 0:
        raise ValueError("chars must be a non-negative exact int")
    if isinstance(chars_per_token, bool) or chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    return int(round(chars / chars_per_token))


def measure_packet_economics(
    *,
    baseline_context: Mapping[str, Any],
    compact_packet: Mapping[str, Any],
    chars_per_token: float = 4.0,
) -> dict[str, Any]:
    baseline_chars = compact_chars(baseline_context)
    packet_chars = compact_chars(compact_packet)
    baseline_tokens = estimate_tokens_from_chars(
        baseline_chars, chars_per_token=chars_per_token
    )
    packet_tokens = estimate_tokens_from_chars(packet_chars, chars_per_token=chars_per_token)
    char_reduction = (
        1.0 - packet_chars / baseline_chars if baseline_chars else 0.0
    )
    token_reduction = (
        1.0 - packet_tokens / baseline_tokens if baseline_tokens else 0.0
    )
    return {
        "schema_version": "qore.packet.economics.v1",
        "baseline_chars": baseline_chars,
        "packet_chars": packet_chars,
        "estimated_baseline_tokens": baseline_tokens,
        "estimated_packet_tokens": packet_tokens,
        "char_reduction_ratio": char_reduction,
        "estimated_token_reduction_ratio": token_reduction,
        "chars_per_token_assumption": chars_per_token,
        "interpretation": "MEASUREMENT_ONLY_NOT_LIVE_ROUTING_AUTHORITY",
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare legacy context and compact packet size.")
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--chars-per-token", type=float, default=4.0)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    if not isinstance(baseline, dict) or not isinstance(packet, dict):
        raise SystemExit("baseline and packet must be JSON objects")
    result = measure_packet_economics(
        baseline_context=baseline,
        compact_packet=packet,
        chars_per_token=args.chars_per_token,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
