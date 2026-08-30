#!/usr/bin/env python3
"""Run GPT-5.6 Sol as QORE Principal Architect over a canonical state snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENDPOINT = "https://api.openai.com/v1/responses"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def validate_decision(decision: dict[str, Any], main_sha: str) -> None:
    if decision.get("schema_version") != "qore.architect.decision.v1":
        raise ValueError("unexpected architect schema version")
    if decision.get("source_main_sha") != main_sha:
        raise ValueError("architect decision is not bound to snapshot main SHA")
    if decision.get("production_authority") is not False:
        raise ValueError("production authority must remain false")
    contract = decision.get("engineering_contract")
    if not isinstance(contract, dict):
        raise ValueError("engineering_contract missing")
    if decision.get("next_actor") == "CODEX" and contract.get("enabled") is not True:
        raise ValueError("CODEX routing requires an enabled engineering contract")
    if decision.get("next_actor") != "CODEX" and contract.get("enabled") is True:
        raise ValueError("engineering contract may be enabled only when next_actor is CODEX in rollout v1")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    key = os.environ.get("OPENAI_SOL_API_KEY", "")
    if not key:
        print("OPENAI_SOL_API_KEY is not configured.", file=sys.stderr)
        return 2

    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    main_sha = snapshot.get("main_sha")
    if not isinstance(main_sha, str) or not SHA_RE.fullmatch(main_sha):
        print("Snapshot main SHA is invalid.", file=sys.stderr)
        return 2

    charter = Path(args.charter).read_text(encoding="utf-8")
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    effort = os.environ.get("SOL_REASONING_EFFORT", "high")
    if effort not in {"medium", "high", "xhigh", "max"}:
        print("Invalid SOL_REASONING_EFFORT.", file=sys.stderr)
        return 2

    body = {
        "model": "gpt-5.6-sol",
        "instructions": charter,
        "input": (
            "This is the canonical read-only QORE state snapshot for this architect cycle. "
            "Reconstruct state from it, read the roadmap and constitution, and choose exactly one safe next action.\n\n"
            + json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
        ),
        "reasoning": {"effort": effort, "context": "current_turn"},
        "text": {
            "verbosity": "low",
            "format": {
                "type": "json_schema",
                "name": "qore_architect_decision",
                "strict": True,
                "schema": schema,
            },
        },
        "max_output_tokens": 5000,
        "store": False,
        "metadata": {"qore_role": "principal_architect", "qore_main_sha": main_sha},
    }

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"OpenAI Sol request failed with HTTP {exc.code}.", file=sys.stderr)
        return 3
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"OpenAI Sol request failed: {type(exc).__name__}", file=sys.stderr)
        return 4

    if payload.get("status") != "completed":
        print(f"OpenAI Sol response did not complete: {payload.get('status')}", file=sys.stderr)
        return 5

    rendered = output_text(payload)
    try:
        decision = json.loads(rendered)
        validate_decision(decision, main_sha)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"Architect decision failed closed: {exc}", file=sys.stderr)
        return 6

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage, dict) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage, dict) else {}
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    safe_usage = {
        "response_id": payload.get("id"),
        "model": payload.get("model"),
        "input_tokens": usage.get("input_tokens"),
        "cached_tokens": input_details.get("cached_tokens"),
        "cache_write_tokens": input_details.get("cache_write_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    Path(args.usage_output).write_text(json.dumps(safe_usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SOL_ARCHITECT_OK main={main_sha} status={decision['status']} next_actor={decision['next_actor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
