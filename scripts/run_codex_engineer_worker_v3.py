#!/usr/bin/env python3
"""Cache-stable bounded GPT-5.3-Codex engineering worker V3.

V3 preserves the stateless/store=false privacy boundary from V2 while keeping
all already-sent conversation items byte-stable across turns so OpenAI prompt
caching can reuse the growing prefix. It also exposes one read-only historical
reference-diff tool restricted to exact 40-hex SHAs explicitly present in the
architect-issued engineering contract.

No GitHub credential, arbitrary shell, network tool, merge/review authority, or
Production authority is given to the model. Publication remains a separate
controller action after an independent full Quality Gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import run_codex_engineer_worker_v2 as v2

MODEL = v2.MODEL
PROMPT_CACHE_KEY = "qore-codex-engineer-worker-v3"
MAX_TURNS = v2.MAX_TURNS
MAX_TOTAL_TOKENS = v2.MAX_TOTAL_TOKENS
MAX_REFERENCE_SHAS = 8
MAX_REFERENCE_DIFF_CHARS = 60_000
REFERENCE_SHA_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])")
TRANSCRIPT_POLICY = "stateless-immutable-cacheable-v1"
WORKER_VERSION = "v3"

REFERENCE_DIFF_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "reference_diff",
    "description": (
        "Read the bounded local git diff from source_main_sha to one exact historical "
        "40-hex SHA explicitly named in the architect engineering contract. Use this "
        "when the contract tells you to preserve or reproduce work from a historical PR/head."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reference_sha": {"type": "string"},
            "max_chars": {"type": "integer"},
        },
        "required": ["reference_sha", "max_chars"],
    },
    "strict": True,
}
TOOLS: list[dict[str, Any]] = [*v2.TOOLS[:-1], REFERENCE_DIFF_TOOL, v2.TOOLS[-1]]


def stable_conversation(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a model-facing copy without retroactively changing prior items.

    Stateless Responses API calls resend history. Prompt caching relies on a
    stable common prefix, so V3 never replaces an older tool output merely
    because newer outputs were appended. The canonical transcript is also not
    mutated by this projection.
    """
    return [dict(item) for item in conversation]


def _walk_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key in sorted(value):
            found.extend(_walk_strings(value[key]))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_strings(item))
    return found


def contract_reference_shas(contract: dict[str, Any], source_main_sha: str) -> tuple[str, ...]:
    """Extract a small deterministic allowlist of exact historical SHAs.

    Only SHAs literally present in the signed/immutable engineering contract
    are exposed. source_main_sha itself is excluded because normal tools already
    operate on that checkout.
    """
    values: set[str] = set()
    for text in _walk_strings(contract):
        values.update(REFERENCE_SHA_RE.findall(text))
    values.discard(source_main_sha)
    ordered = tuple(sorted(values))
    if len(ordered) > MAX_REFERENCE_SHAS:
        raise v2.WorkerError("engineering contract contains too many historical SHA references")
    return ordered


class LocalToolsV3(v2.LocalTools):
    def __init__(self, repo: Path, source_main_sha: str, allowed_reference_shas: tuple[str, ...]) -> None:
        super().__init__(repo)
        self.source_main_sha = source_main_sha
        self.allowed_reference_shas = frozenset(allowed_reference_shas)

    def reference_diff(self, reference_sha: str, max_chars: int) -> dict[str, Any]:
        if not isinstance(reference_sha, str) or v2.SHA_RE.fullmatch(reference_sha) is None:
            raise v2.WorkerError("reference_sha must be an exact 40-hex commit SHA")
        if reference_sha not in self.allowed_reference_shas:
            raise v2.WorkerError("reference_sha is not explicitly allowlisted by the engineering contract")
        if not 1_000 <= max_chars <= MAX_REFERENCE_DIFF_CHARS:
            raise v2.WorkerError(f"max_chars must be 1000..{MAX_REFERENCE_DIFF_CHARS}")

        exists = v2.run_process(
            ["git", "cat-file", "-e", f"{reference_sha}^{{commit}}"],
            cwd=self.repo,
            timeout=60,
        )
        if exists.returncode != 0:
            raise v2.WorkerError("allowlisted historical reference is not present in the local clone")

        changed = v2.git(
            self.repo,
            "diff",
            "--name-only",
            self.source_main_sha,
            reference_sha,
            "--",
            timeout=60,
        ).splitlines()
        diff = v2.git(
            self.repo,
            "diff",
            "--binary",
            "--no-ext-diff",
            self.source_main_sha,
            reference_sha,
            "--",
            timeout=60,
        )
        return {
            "source_main_sha": self.source_main_sha,
            "reference_sha": reference_sha,
            "changed_files": sorted(path for path in changed if path),
            "diff": v2.clip(diff, max_chars),
        }


def dispatch_tool(tools: LocalToolsV3, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "reference_diff":
        return tools.reference_diff(str(args["reference_sha"]), int(args["max_chars"]))
    return v2.dispatch_tool(tools, name, args)


def response_usage(payload: dict[str, Any]) -> dict[str, int]:
    """Return non-sensitive per-response usage counters for diagnostics."""
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_details = (
        usage.get("input_tokens_details")
        if isinstance(usage.get("input_tokens_details"), dict)
        else {}
    )
    output_details = (
        usage.get("output_tokens_details")
        if isinstance(usage.get("output_tokens_details"), dict)
        else {}
    )
    result: dict[str, int] = {}
    for key, value in (
        ("input_tokens", usage.get("input_tokens")),
        ("cached_tokens", input_details.get("cached_tokens")),
        ("cache_write_tokens", input_details.get("cache_write_tokens")),
        ("output_tokens", usage.get("output_tokens")),
        ("reasoning_tokens", output_details.get("reasoning_tokens")),
        ("total_tokens", usage.get("total_tokens")),
    ):
        if type(value) is int and value >= 0:
            result[key] = value
    return result


def append_trace(
    trace: list[dict[str, Any]],
    *,
    turn: int,
    tool_name: str,
    payload: dict[str, Any],
    usage_total: dict[str, int],
    tool_output_chars: int | None,
) -> None:
    """Record only safe operational metadata, never tool arguments or contents."""
    item: dict[str, Any] = {
        "turn": turn,
        "tool": tool_name,
        "budget_tokens_after": v2.spend_equivalent_tokens(usage_total),
        **response_usage(payload),
    }
    if tool_output_chars is not None:
        item["tool_output_chars"] = tool_output_chars
    trace.append(item)


def make_budget_block(
    repo: Path,
    source: str,
    contract_id: str,
    tools: LocalToolsV3,
    turns: int,
    summary: str,
) -> dict[str, Any]:
    return v2.make_result(
        repo,
        source,
        contract_id,
        "BLOCKED",
        summary,
        [
            f"MAX_TOTAL_TOKENS={MAX_TOTAL_TOKENS} input-equivalent units",
            "Raw API usage remains preserved in the usage artifact.",
            "No candidate was published.",
        ],
        tools,
        turns,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--charter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--usage-output", required=True)
    args = parser.parse_args()

    key = os.environ.get("OPENAI_CODEX_API_KEY", "").strip()
    if not key:
        print("OPENAI_CODEX_API_KEY is not configured.", file=sys.stderr)
        return 2

    repo = Path(args.repo_dir).resolve()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    source, contract = v2.validate_request(request, repo)
    contract_id = str(contract.get("contract_id") or "")
    if not contract_id:
        raise v2.WorkerError("contract_id is required")
    reference_shas = contract_reference_shas(contract, source)
    charter = Path(args.charter).read_text(encoding="utf-8")
    tools = LocalToolsV3(repo, source, reference_shas)

    reference_guidance = (
        " If the engineering contract names a historical 40-hex PR/head SHA, use reference_diff "
        "early instead of attempting to rediscover that historical delta from the current checkout."
        if reference_shas
        else ""
    )
    prompt = (
        "Execute this architect-issued engineering contract against the exact local checkout. "
        "This is bounded implementation, not PLAN-ONLY. Inspect only what is needed, then implement; "
        "use apply_patch for changes. You have no arbitrary shell, network, GitHub credential, "
        "merge/review authority, or Production authority. Never weaken tests or validation. "
        "Run run_quality_gate after the final patch before READY. If the contract cannot be safely "
        "completed in scope, finish BLOCKED with the concrete missing capability or contradiction."
        + reference_guidance
        + "\n\nENGINEERING_REQUEST:\n"
        + json.dumps(request, separators=(",", ":"), ensure_ascii=False)
    )
    conversation: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
    ]
    usage_total: dict[str, int] = {}
    response_ids: list[str] = []
    turn_trace: list[dict[str, Any]] = []
    final: dict[str, Any] | None = None
    completed_turns = 0

    for turn in range(1, MAX_TURNS + 1):
        if v2.spend_equivalent_tokens(usage_total) >= MAX_TOTAL_TOKENS:
            final = make_budget_block(
                repo,
                source,
                contract_id,
                tools,
                completed_turns,
                "Codex worker stopped at the spend-equivalent API token budget.",
            )
            break

        payload = v2.api_call(
            key,
            {
                "model": MODEL,
                "instructions": charter,
                "input": stable_conversation(conversation),
                "tools": TOOLS,
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "reasoning": {"effort": "high"},
                "max_output_tokens": 7000,
                "store": False,
                "prompt_cache_key": PROMPT_CACHE_KEY,
                "metadata": {
                    "qore_role": "principal_engineer_worker_v3",
                    "qore_main_sha": source,
                    "contract_id": contract_id[:64],
                },
            },
        )
        completed_turns = turn
        if payload.get("status") != "completed":
            raise v2.WorkerError(f"Codex response did not complete: {payload.get('status')}")
        if isinstance(payload.get("id"), str):
            response_ids.append(payload["id"])
        v2.add_usage(usage_total, payload)

        outputs = payload.get("output")
        if not isinstance(outputs, list):
            raise v2.WorkerError("Codex response output is invalid")
        calls = [
            item
            for item in outputs
            if isinstance(item, dict) and item.get("type") == "function_call"
        ]
        if len(calls) != 1:
            raise v2.WorkerError(f"expected exactly one function call, got {len(calls)}")
        call = calls[0]
        name, call_id, raw_args = call.get("name"), call.get("call_id"), call.get("arguments")
        if not all(isinstance(value, str) for value in (name, call_id, raw_args)):
            raise v2.WorkerError("function call shape is invalid")
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise v2.WorkerError("function arguments are invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise v2.WorkerError("function arguments must be an object")
        conversation.extend(item for item in outputs if isinstance(item, dict))

        if name == "finish":
            append_trace(
                turn_trace,
                turn=turn,
                tool_name=name,
                payload=payload,
                usage_total=usage_total,
                tool_output_chars=None,
            )
            status, summary, notes = parsed.get("status"), parsed.get("summary"), parsed.get("notes")
            if status not in {"READY", "BLOCKED"} or not isinstance(summary, str) or not isinstance(notes, list):
                raise v2.WorkerError("finish arguments are invalid")
            if status == "READY":
                if not v2.changed_files(repo):
                    raise v2.WorkerError("READY requires a non-empty candidate")
                if not tools.last_quality_success:
                    raise v2.WorkerError("READY requires green full gate after final patch")
            final = v2.make_result(
                repo,
                source,
                contract_id,
                status,
                summary,
                [str(item) for item in notes],
                tools,
                turn,
            )
            break

        try:
            result = dispatch_tool(tools, name, parsed)
            tool_output: dict[str, Any] = {"ok": True, "result": result}
        except (v2.WorkerError, OSError, subprocess.TimeoutExpired) as exc:
            tool_output = {"ok": False, "error": str(exc)}
        rendered_output = v2.clip(json.dumps(tool_output, ensure_ascii=False))
        conversation.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": rendered_output,
            }
        )
        append_trace(
            turn_trace,
            turn=turn,
            tool_name=name,
            payload=payload,
            usage_total=usage_total,
            tool_output_chars=len(rendered_output),
        )

        if v2.spend_equivalent_tokens(usage_total) >= MAX_TOTAL_TOKENS:
            final = make_budget_block(
                repo,
                source,
                contract_id,
                tools,
                turn,
                "Codex worker reached the spend-equivalent API token budget after the latest bounded tool action.",
            )
            break

    if final is None:
        final = v2.make_result(
            repo,
            source,
            contract_id,
            "BLOCKED",
            "Codex worker reached the hard turn limit without a terminal engineering result.",
            [f"MAX_TURNS={MAX_TURNS}", "No candidate was published."],
            tools,
            completed_turns,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    raw_total_tokens = usage_total.get("total_tokens", 0)
    usage_total.update(
        {
            "model": MODEL,
            "worker_version": WORKER_VERSION,
            "transcript_policy": TRANSCRIPT_POLICY,
            "prompt_cache_key": PROMPT_CACHE_KEY,
            "response_ids": response_ids,
            "turns": final["turns"],
            "max_turns": MAX_TURNS,
            "max_total_tokens": MAX_TOTAL_TOKENS,
            "budget_formula_version": v2.BUDGET_FORMULA_VERSION,
            "budget_tokens": v2.spend_equivalent_tokens(usage_total),
            "raw_total_tokens": raw_total_tokens,
            "reference_shas": list(reference_shas),
            "turn_trace": turn_trace,
        }
    )
    Path(args.usage_output).write_text(
        json.dumps(usage_total, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"CODEX_ENGINEER_WORKER_V3_OK status={final['status']} main={source} "
        f"contract={contract_id} changed_files={len(final['changed_files'])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v2.WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_V3_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(7) from exc
