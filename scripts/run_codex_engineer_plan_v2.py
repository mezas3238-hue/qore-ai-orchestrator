#!/usr/bin/env python3
"""Run Codex as QORE Principal Engineer over bounded engineering context."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENDPOINT = "https://api.openai.com/v1/responses"
PROMPT_CACHE_KEY = "qore-codex-principal-engineer-v1"


def output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()
    key = os.environ.get("OPENAI_CODEX_API_KEY", "")
    if not key:
        print("OPENAI_CODEX_API_KEY is not configured.", file=sys.stderr)
        return 2
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    contract = decision.get("engineering_contract")
    if decision.get("next_actor") != "CODEX" or not isinstance(contract, dict) or contract.get("enabled") is not True:
        print("Architect decision does not authorize a Codex engineering contract.", file=sys.stderr)
        return 2
    if decision.get("source_main_sha") != snapshot.get("main_sha"):
        print("Architect decision and snapshot SHA do not match.", file=sys.stderr)
        return 2
    engineer_context = snapshot.get("engineer_context")
    if not isinstance(engineer_context, dict):
        print("Bounded engineering context is missing.", file=sys.stderr)
        return 2
    charter = Path(args.charter).read_text(encoding="utf-8")
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    body = {
        "model": "gpt-5.3-codex",
        "instructions": charter,
        "input": (
            "Prepare the implementation plan for this architect-issued contract. This rollout is PLAN-ONLY. "
            "Use only the bounded engineering evidence supplied here to identify likely files/tests. If material evidence is missing, say so in the plan rather than inventing it. Do not claim to have modified code.\n\n"
            "ARCHITECT_DECISION:\n" + json.dumps(decision, separators=(",", ":"), ensure_ascii=False)
            + "\n\nBOUNDED_ENGINEERING_CONTEXT:\n" + json.dumps(engineer_context, separators=(",", ":"), ensure_ascii=False)
        ),
        "reasoning": {"effort": "high"},
        "text": {"verbosity": "low", "format": {"type": "json_schema", "name": "qore_codex_plan", "strict": True, "schema": schema}},
        "max_output_tokens": 4500,
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        "metadata": {"qore_role": "principal_engineer_plan_only", "qore_main_sha": str(snapshot.get("main_sha", ""))},
    }
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"OpenAI Codex request failed with HTTP {exc.code}.", file=sys.stderr)
        return 3
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"OpenAI Codex request failed: {type(exc).__name__}", file=sys.stderr)
        return 4
    if payload.get("status") != "completed":
        print(f"OpenAI Codex response did not complete: {payload.get('status')}", file=sys.stderr)
        return 5
    rendered = output_text(payload)
    try:
        plan = json.loads(rendered)
    except json.JSONDecodeError:
        print("Codex plan was not valid JSON.", file=sys.stderr)
        return 6
    if plan.get("schema_version") != "qore.codex.plan.v1":
        print("Unexpected Codex plan schema version.", file=sys.stderr)
        return 6
    if plan.get("source_main_sha") != snapshot.get("main_sha"):
        print("Codex plan is not bound to snapshot main SHA.", file=sys.stderr)
        return 6
    if plan.get("contract_id") != contract.get("contract_id"):
        print("Codex plan contract ID mismatch.", file=sys.stderr)
        return 6
    if plan.get("production_authority") is not False:
        print("Codex plan attempted to change Production authority.", file=sys.stderr)
        return 6
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage, dict) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage, dict) else {}
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    safe_usage = {
        "response_id": payload.get("id"), "model": payload.get("model"), "input_tokens": usage.get("input_tokens"),
        "cached_tokens": input_details.get("cached_tokens"), "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": output_details.get("reasoning_tokens"), "total_tokens": usage.get("total_tokens"),
        "prompt_cache_key": PROMPT_CACHE_KEY, "model_context_chars": metrics.get("engineer_context_chars"),
        "full_snapshot_chars": metrics.get("full_snapshot_chars"),
    }
    Path(args.usage_output).write_text(json.dumps(safe_usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_ENGINEER_PLAN_OK main={plan['source_main_sha']} status={plan['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
