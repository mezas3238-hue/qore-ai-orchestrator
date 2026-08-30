#!/usr/bin/env python3
"""Run GPT-5.6 Sol as QORE Principal Architect over bounded model context."""

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
EFFORTS = ("medium", "high", "xhigh", "max")
EFFORT_RANK = {name: index for index, name in enumerate(EFFORTS)}
PROMPT_CACHE_KEY = "qore-sol-principal-architect-v1"


def output_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    return "".join(chunks).strip()


def _disabled_review_contract(contract: dict[str, Any]) -> bool:
    return (
        contract.get("enabled") is False
        and contract.get("review_kind") == "NONE"
        and contract.get("pr_number") == 0
        and contract.get("scope") == []
        and contract.get("adversarial_foci") == []
        and contract.get("acceptance") == []
        and contract.get("forbidden") == []
    )


def validate_decision(decision: dict[str, Any], main_sha: str, effort: str) -> None:
    if decision.get("schema_version") != "qore.architect.decision.v1":
        raise ValueError("unexpected architect schema version")
    if decision.get("source_main_sha") != main_sha:
        raise ValueError("architect decision is not bound to snapshot main SHA")
    if decision.get("production_authority") is not False:
        raise ValueError("production authority must remain false")
    assessment = decision.get("reasoning_assessment")
    if not isinstance(assessment, dict):
        raise ValueError("reasoning_assessment missing")
    if assessment.get("effort_used") != effort:
        raise ValueError("reasoning assessment does not match controller-selected effort")
    target = assessment.get("target_effort")
    if target not in EFFORT_RANK:
        raise ValueError("invalid reasoning escalation target")
    escalation = assessment.get("escalation_requested")
    if type(escalation) is not bool:
        raise ValueError("escalation_requested must be a boolean")
    if escalation:
        if EFFORT_RANK[target] <= EFFORT_RANK[effort]:
            raise ValueError("reasoning escalation target must be strictly higher")
    elif target != effort:
        raise ValueError("non-escalating decision must keep target_effort equal to effort_used")
    engineering = decision.get("engineering_contract")
    review = decision.get("review_contract")
    if not isinstance(engineering, dict):
        raise ValueError("engineering_contract missing")
    if not isinstance(review, dict):
        raise ValueError("review_contract missing")
    actor = decision.get("next_actor")
    status = decision.get("status")
    if actor == "CODEX":
        if status != "ENGINEERING_TASK" or engineering.get("enabled") is not True:
            raise ValueError("CODEX routing requires ENGINEERING_TASK and enabled engineering contract")
        if not _disabled_review_contract(review):
            raise ValueError("CODEX routing cannot also enable an external review contract")
    elif engineering.get("enabled") is True:
        raise ValueError("engineering contract may be enabled only when next_actor is CODEX")
    if actor in {"CLAUDE_CODE", "DEEPSEEK"}:
        if status != "REVIEW_TASK" or review.get("enabled") is not True:
            raise ValueError("external reviewer routing requires REVIEW_TASK and enabled review contract")
        if type(review.get("pr_number")) is not int or review["pr_number"] <= 0:
            raise ValueError("external reviewer routing requires a positive PR number")
        if actor == "CLAUDE_CODE" and review.get("review_kind") != "CLAUDE_TECHNICAL":
            raise ValueError("CLAUDE_CODE routing requires CLAUDE_TECHNICAL review kind")
        if actor == "DEEPSEEK" and review.get("review_kind") not in {"DEEPSEEK_EXPERT", "DEEPSEEK_CODER"}:
            raise ValueError("DEEPSEEK routing requires an Expert or Coder review kind")
        if engineering.get("enabled") is True:
            raise ValueError("external reviewer routing cannot also enable engineering")
    elif not _disabled_review_contract(review):
        raise ValueError("review contract must be disabled when next_actor is not an external reviewer")


def _model_input(snapshot: dict[str, Any], effort: str) -> Any:
    stable = snapshot.get("stable_context")
    dynamic = snapshot.get("dynamic_context")
    if not isinstance(stable, dict) or not isinstance(dynamic, dict):
        raise ValueError("bounded model context is missing stable/dynamic sections")
    stable_text = (
        "STABLE QORE ARCHITECTURAL CORPUS. This corpus is authoritative for this cycle and is placed first to maximize safe prompt-cache reuse.\n\n"
        + json.dumps(stable, separators=(",", ":"), ensure_ascii=False)
    )
    dynamic_text = (
        f"The deterministic controller selected reasoning effort `{effort}` for this pass. "
        "Report that exact value in reasoning_assessment.effort_used. If the supplied evidence makes a higher tier materially necessary, request exactly one escalation according to the charter; otherwise keep target_effort equal to effort_used.\n\n"
        "LIVE BOUNDED QORE STATE. Reconstruct current state from this evidence, use the roadmap and constitution from the stable corpus, and choose exactly one safe next action. The full canonical snapshot is preserved as a separate cycle artifact. If omitted backlog detail is materially necessary, request evidence instead of inferring it.\n\n"
        + json.dumps(dynamic, separators=(",", ":"), ensure_ascii=False)
    )
    return [{"role": "user", "content": [
        {"type": "input_text", "text": stable_text},
        {"type": "input_text", "text": dynamic_text},
    ]}]


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
    if snapshot.get("schema_version") != "qore.model.context.v1":
        print("Unexpected bounded model-context schema.", file=sys.stderr)
        return 2
    main_sha = snapshot.get("main_sha")
    if not isinstance(main_sha, str) or not SHA_RE.fullmatch(main_sha):
        print("Snapshot main SHA is invalid.", file=sys.stderr)
        return 2
    dynamic = snapshot.get("dynamic_context")
    if not isinstance(dynamic, dict) or dynamic.get("source_main_sha") != main_sha:
        print("Model context main SHA binding is invalid.", file=sys.stderr)
        return 2
    charter = Path(args.charter).read_text(encoding="utf-8")
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    effort = os.environ.get("SOL_REASONING_EFFORT", "high")
    if effort not in EFFORT_RANK:
        print("Invalid SOL_REASONING_EFFORT.", file=sys.stderr)
        return 2
    try:
        max_output_tokens = int(os.environ.get("SOL_MAX_OUTPUT_TOKENS", "5500"))
    except ValueError:
        print("Invalid SOL_MAX_OUTPUT_TOKENS.", file=sys.stderr)
        return 2
    if max_output_tokens < 1000 or max_output_tokens > 20000:
        print("SOL_MAX_OUTPUT_TOKENS outside bounded range.", file=sys.stderr)
        return 2
    body = {
        "model": "gpt-5.6-sol",
        "instructions": charter,
        "input": _model_input(snapshot, effort),
        "reasoning": {"effort": effort, "context": "current_turn"},
        "text": {"verbosity": "low", "format": {"type": "json_schema", "name": "qore_architect_decision", "strict": True, "schema": schema}},
        "max_output_tokens": max_output_tokens,
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
        "metadata": {"qore_role": "principal_architect", "qore_main_sha": main_sha, "qore_reasoning_effort": effort},
    }
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode("utf-8"), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=420) as response:
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
        validate_decision(decision, main_sha, effort)
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
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    safe_usage = {
        "response_id": payload.get("id"), "model": payload.get("model"), "reasoning_effort": effort,
        "max_output_tokens": max_output_tokens, "input_tokens": usage.get("input_tokens"),
        "cached_tokens": input_details.get("cached_tokens"), "cache_write_tokens": input_details.get("cache_write_tokens"),
        "output_tokens": usage.get("output_tokens"), "reasoning_tokens": output_details.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"), "prompt_cache_key": PROMPT_CACHE_KEY,
        "model_context_chars": metrics.get("architect_context_chars"), "full_snapshot_chars": metrics.get("full_snapshot_chars"),
    }
    Path(args.usage_output).write_text(json.dumps(safe_usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("SOL_ARCHITECT_OK main={} effort={} status={} next_actor={}".format(main_sha, effort, decision["status"], decision["next_actor"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
