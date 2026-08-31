#!/usr/bin/env python3
"""GPT-5.6 Sol V3: one high-value architect call over an immutable compact packet.

V3 is intentionally not a replacement for broad roadmap reconstruction. It may
only run when the deterministic controller has already produced a specific
qore.sol.decision.packet.v2 bound to one Work Unit or frozen Candidate. The
stable role/constitution/invariant prefix is separate and cacheable; the packet
is appended after the cache breakpoint. No hidden retry, Production authority,
or reviewer suppression is introduced.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

import run_sol_architect_v2 as v2

ENDPOINT = v2.ENDPOINT
MODEL = "gpt-5.6-sol"
MAX_PACKET_CHARS = 60_000
MAX_STABLE_PREFIX_CHARS = 90_000
ALLOWED_ACTIVATION_MODES = {"CONTROLLED_VALIDATION", "LIMITED_LIVE"}


def _compact_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def validate_packet(packet: Mapping[str, Any]) -> tuple[str, str, str]:
    if packet.get("schema_version") != "qore.sol.decision.packet.v2":
        raise ValueError("unexpected Sol decision packet schema")
    if packet.get("production_authority") is not False:
        raise ValueError("decision packet attempted Production authority")
    subject_kind = packet.get("subject_kind")
    subject = packet.get("subject")
    subject_id = packet.get("subject_id")
    if subject_kind not in {"WORK_UNIT", "CANDIDATE"} or not isinstance(subject, Mapping):
        raise ValueError("decision packet subject is invalid")
    if not isinstance(subject_id, str) or not subject_id:
        raise ValueError("decision packet subject_id is invalid")
    if subject_kind == "WORK_UNIT":
        source_main = subject.get("source_main_sha")
    else:
        source_main = subject.get("base_sha")
    if not isinstance(source_main, str) or v2.SHA_RE.fullmatch(source_main) is None:
        raise ValueError("decision packet source/base SHA is invalid")
    decision_required = packet.get("decision_required")
    if not isinstance(decision_required, str) or not decision_required.strip():
        raise ValueError("decision_required must be explicit and non-empty")
    if _compact_chars(packet) > MAX_PACKET_CHARS:
        raise ValueError("decision packet exceeds compact V3 hard bound")
    return subject_kind, source_main, subject_id


def validate_stable_prefix(prefix: Mapping[str, Any]) -> tuple[str, str]:
    if prefix.get("schema_version") != "qore.stable.prompt.prefix.v1":
        raise ValueError("unexpected stable-prefix schema")
    if prefix.get("production_authority") is not False:
        raise ValueError("stable prefix attempted Production authority")
    text = prefix.get("prefix_text")
    cache_key = prefix.get("prompt_cache_key")
    digest = prefix.get("prefix_sha256")
    if not isinstance(text, str) or not text:
        raise ValueError("stable prefix text is empty")
    if len(text) > MAX_STABLE_PREFIX_CHARS:
        raise ValueError("stable prefix exceeds V3 hard bound")
    if not isinstance(cache_key, str) or not cache_key:
        raise ValueError("stable prefix cache key is invalid")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("stable prefix digest is invalid")
    return text, cache_key


def _model_input(
    *,
    stable_text: str,
    packet: Mapping[str, Any],
    effort: str,
) -> list[dict[str, Any]]:
    dynamic = (
        f"The deterministic controller selected reasoning effort `{effort}`. "
        "You are receiving one exact decision packet, not a whole-repository reconstruction. "
        "Answer only the decision_required question using supplied evidence. If material evidence is missing, "
        "return RECONSTRUCTION_REQUIRED with precise evidence_requests instead of inferring. Existing reviewer "
        "verdicts are evidence, never authority. No decision grants Production authority.\n\n"
        "QORE_SOL_DECISION_PACKET_V2:\n"
        + json.dumps(packet, separators=(",", ":"), ensure_ascii=False)
    )
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": stable_text,
                    "prompt_cache_breakpoint": {"mode": "explicit"},
                },
                {"type": "input_text", "text": dynamic},
            ],
        }
    ]


def safe_usage_record(
    payload: Mapping[str, Any],
    *,
    effort: str,
    packet: Mapping[str, Any],
    prefix: Mapping[str, Any],
    max_output_tokens: int,
) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), Mapping) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), Mapping) else {}
    incomplete = payload.get("incomplete_details") if isinstance(payload.get("incomplete_details"), Mapping) else {}
    error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
    return {
        "schema_version": "qore.sol.v3.usage.v1",
        "response_id": payload.get("id"),
        "model": payload.get("model"),
        "response_status": v2._safe_code(payload.get("status")) or "unknown",
        "incomplete_reason": v2._safe_code(incomplete.get("reason")),
        "response_error_code": v2._safe_code(error.get("code")),
        "reasoning_effort": effort,
        "max_output_tokens": max_output_tokens,
        "input_tokens": usage.get("input_tokens"),
        "cached_tokens": input_details.get("cached_tokens"),
        "cache_write_tokens": input_details.get("cache_write_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "prompt_cache_key": prefix.get("prompt_cache_key"),
        "stable_prefix_sha256": prefix.get("prefix_sha256"),
        "stable_prefix_chars": len(str(prefix.get("prefix_text") or "")),
        "packet_id": packet.get("packet_id"),
        "packet_sha256": packet.get("packet_sha256"),
        "packet_chars": _compact_chars(packet),
        "model_calls": 1,
        "production_authority": False,
    }


def execute_v3(
    *,
    key: str,
    packet: Mapping[str, Any],
    stable_prefix: Mapping[str, Any],
    charter: str,
    schema: Mapping[str, Any],
    effort: str,
    max_output_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _, source_main, _ = validate_packet(packet)
    stable_text, cache_key = validate_stable_prefix(stable_prefix)
    if effort not in v2.EFFORT_RANK:
        raise ValueError("invalid reasoning effort")
    if max_output_tokens < 1000 or max_output_tokens > 20_000:
        raise ValueError("max_output_tokens outside bounded range")

    body = {
        "model": MODEL,
        "instructions": charter,
        "input": _model_input(stable_text=stable_text, packet=packet, effort=effort),
        "reasoning": {"effort": effort, "context": "current_turn"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "qore_architect_decision",
                "strict": True,
                "schema": dict(schema),
            },
        },
        "max_output_tokens": max_output_tokens,
        "store": False,
        "prompt_cache_key": cache_key,
        "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
        "metadata": {
            "qore_role": "principal_architect_compact_v3",
            "qore_main_sha": source_main,
            "qore_reasoning_effort": effort,
            "qore_packet_id": str(packet.get("packet_id") or "")[:64],
        },
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=420) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OpenAI Sol V3 request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenAI Sol V3 request failed: {type(exc).__name__}") from exc

    usage = safe_usage_record(
        payload,
        effort=effort,
        packet=packet,
        prefix=stable_prefix,
        max_output_tokens=max_output_tokens,
    )
    if payload.get("status") != "completed":
        raise RuntimeError("OpenAI Sol V3 response did not complete")
    rendered = v2.output_text(payload)
    try:
        decision = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ValueError("Sol V3 structured output is invalid JSON") from exc
    if not isinstance(decision, dict):
        raise ValueError("Sol V3 decision must be an object")
    v2.validate_decision(decision, source_main, effort)
    return decision, usage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", required=True)
    parser.add_argument("--stable-prefix", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    activation = os.environ.get("QORE_SOL_V3_MODE", "").strip()
    if activation not in ALLOWED_ACTIVATION_MODES:
        print("QORE_SOL_V3_MODE must be CONTROLLED_VALIDATION or LIMITED_LIVE.", file=sys.stderr)
        return 2
    key = os.environ.get("OPENAI_SOL_API_KEY", "").strip()
    if not key:
        print("OPENAI_SOL_API_KEY is not configured.", file=sys.stderr)
        return 2
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    prefix = json.loads(Path(args.stable_prefix).read_text(encoding="utf-8"))
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    charter = Path(args.charter).read_text(encoding="utf-8")
    if not isinstance(packet, dict) or not isinstance(prefix, dict) or not isinstance(schema, dict):
        print("Sol V3 JSON inputs must be objects.", file=sys.stderr)
        return 2
    effort = os.environ.get("SOL_REASONING_EFFORT", "high")
    max_output_tokens = int(os.environ.get("SOL_MAX_OUTPUT_TOKENS", "8000"))
    try:
        decision, usage = execute_v3(
            key=key,
            packet=packet,
            stable_prefix=prefix,
            charter=charter,
            schema=schema,
            effort=effort,
            max_output_tokens=max_output_tokens,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"SOL_ARCHITECT_V3_ERROR: {exc}", file=sys.stderr)
        return 6
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.usage_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.usage_output).write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "SOL_ARCHITECT_V3_OK packet={} status={} next_actor={} calls=1".format(
            packet.get("packet_id"), decision.get("status"), decision.get("next_actor")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
