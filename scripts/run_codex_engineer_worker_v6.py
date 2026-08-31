#!/usr/bin/env python3
"""Codex worker V6: deterministic evidence first, at most two high-value model calls.

V6 is an opt-in candidate worker. It does not change the live V5 workflow in this
change set. The controller validates the exact source checkout, optionally
materializes one exact historical reference using the already-hardened V4
mechanism, constructs bounded evidence without model exploration, requests one
structured patch/need-evidence/block decision, applies only allowlisted paths,
and runs the immutable full QORE Quality Gate. One and only one continuation is
permitted either for deterministic evidence requested by the first response or
for a correction after a failed Quality Gate.

No arbitrary shell/network/GitHub credential is exposed to the model. No hidden
retry, merge/review authority, Production authority, or reviewer reduction is
introduced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import run_codex_engineer_worker_v2 as v2
import run_codex_engineer_worker_v4 as v4
from source_symbol_slicer import slice_python_symbol

WORKER_VERSION = "v6"
MODEL = v2.MODEL
PROMPT_CACHE_KEY = "qore-codex-engineer-worker-v6"
MAX_MODEL_CALLS = 2
MAX_TOTAL_TOKENS = 80_000
MAX_EVIDENCE_CHARS = 180_000
MAX_FILE_CHARS = 50_000
MAX_CURRENT_DIFF_CHARS = 60_000
MAX_QG_FAILURE_CHARS = 24_000
# The final path character cannot be punctuation that commonly terminates prose.
# This prevents a sentence such as "add src/qore/new_file.py." from granting a
# second synthetic path ending in a period while preserving dots inside names.
PATH_RE = re.compile(
    r"(?P<path>(?:src|tests|docs|schemas|scripts|charters)/[A-Za-z0-9_.@/+\-]*[A-Za-z0-9_@+\-])"
)
EVIDENCE_REQUEST_RE = re.compile(
    r"^(?P<kind>file|test|symbol):(?P<path>[^#]+?)(?:#(?P<symbol>[A-Za-z_][A-Za-z0-9_.]*))?$"
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["PATCH", "NEED_EVIDENCE", "BLOCKED"]},
        "patch": {"type": "string"},
        "evidence_requests": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "summary": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
    },
    "required": ["action", "patch", "evidence_requests", "summary", "notes"],
}


def _output_text(payload: Mapping[str, Any]) -> str:
    chunks: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        raise v2.WorkerError("Codex V6 response output is invalid")
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    rendered = "".join(chunks).strip()
    if not rendered:
        raise v2.WorkerError("Codex V6 response contains no structured output text")
    return rendered


def _contract_text(contract: Mapping[str, Any]) -> str:
    values: list[str] = []
    for key in ("objective", "scope", "acceptance", "required_tests", "forbidden"):
        value = contract.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, str))
    return "\n".join(values)


def contract_paths(contract: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted({match.group("path") for match in PATH_RE.finditer(_contract_text(contract))}))


def _bounded_text_file(repo: Path, rel: str) -> dict[str, Any]:
    target = v2.safe_path(repo, rel)
    if not target.is_file() or target.is_symlink():
        raise v2.WorkerError(f"evidence path is not a regular file: {rel}")
    if target.stat().st_size > 1_500_000:
        raise v2.WorkerError(f"evidence file exceeds hard byte bound: {rel}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise v2.WorkerError(f"evidence file is not UTF-8: {rel}") from exc
    content = text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS]
    return {
        "path": rel,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "chars": len(text),
        "truncated": len(text) > MAX_FILE_CHARS,
        "content": content,
    }


def _candidate_diff(repo: Path) -> dict[str, Any]:
    raw = v2.git(repo, "diff", "--binary", "--no-ext-diff", "--", timeout=60)
    clipped = raw if len(raw) <= MAX_CURRENT_DIFF_CHARS else raw[:MAX_CURRENT_DIFF_CHARS]
    return {
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "chars": len(raw),
        "truncated": len(raw) > MAX_CURRENT_DIFF_CHARS,
        "content": clipped,
        "changed_files": v2.changed_files(repo),
    }


def _initial_evidence(
    repo: Path,
    contract: Mapping[str, Any],
    materialization: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    explicit = set(contract_paths(contract))
    if materialization is not None:
        changed = materialization.get("changed_files")
        if isinstance(changed, list):
            explicit.update(path for path in changed if isinstance(path, str))
    required_tests = contract.get("required_tests")
    if isinstance(required_tests, list):
        for item in required_tests:
            if isinstance(item, str):
                explicit.update(match.group("path") for match in PATH_RE.finditer(item))

    existing: list[str] = []
    missing: list[str] = []
    for rel in sorted(explicit):
        try:
            target = v2.safe_path(repo, rel, must_exist=False)
        except v2.WorkerError:
            raise
        if target.is_file() and not target.is_symlink():
            existing.append(rel)
        else:
            missing.append(rel)
    if len(existing) > v2.MAX_CHANGED_FILES:
        raise v2.WorkerError("initial evidence path set exceeds changed-file hard bound")

    files: list[dict[str, Any]] = []
    total = 0
    for rel in existing:
        item = _bounded_text_file(repo, rel)
        rendered = len(json.dumps(item, separators=(",", ":"), ensure_ascii=False))
        if total + rendered > MAX_EVIDENCE_CHARS:
            break
        files.append(item)
        total += rendered

    allowlist = tuple(sorted(set(existing) | set(materialization.get("changed_files", []) if materialization else [])))
    return {
        "files": files,
        "missing_contract_paths": missing,
        "current_diff": _candidate_diff(repo),
        "materialization": dict(materialization) if materialization is not None else None,
        "evidence_chars": total,
    }, allowlist


def resolve_evidence_requests(repo: Path, requests: Sequence[str]) -> list[dict[str, Any]]:
    if len(requests) > 12:
        raise v2.WorkerError("too many evidence requests")
    results: list[dict[str, Any]] = []
    total = 0
    seen: set[str] = set()
    for request in requests:
        if not isinstance(request, str) or request in seen:
            continue
        seen.add(request)
        match = EVIDENCE_REQUEST_RE.fullmatch(request.strip())
        if match is None:
            raise v2.WorkerError(
                "evidence request must use file:path, test:path, or symbol:path#Symbol"
            )
        kind = match.group("kind")
        rel = Path(match.group("path")).as_posix()
        if kind == "symbol":
            symbol = match.group("symbol")
            if not symbol or not rel.endswith(".py"):
                raise v2.WorkerError("symbol evidence requires a Python path and symbol")
            try:
                item = slice_python_symbol(root=repo, relative_path=rel, symbol=symbol)
            except (ValueError, OSError, SyntaxError) as exc:
                raise v2.WorkerError(f"could not resolve symbol evidence {request}: {exc}") from exc
            wrapped = {"request": request, "kind": kind, "evidence": item}
        else:
            wrapped = {"request": request, "kind": kind, "evidence": _bounded_text_file(repo, rel)}
        rendered = len(json.dumps(wrapped, separators=(",", ":"), ensure_ascii=False))
        if total + rendered > MAX_EVIDENCE_CHARS:
            raise v2.WorkerError("requested evidence exceeds bounded context budget")
        results.append(wrapped)
        total += rendered
    return results


def _apply_allowlisted_patch(tools: v2.LocalTools, patch: str, *, allowlist: Sequence[str]) -> dict[str, Any]:
    paths = v2.validate_patch_paths(patch)
    forbidden = sorted(set(paths) - set(allowlist))
    if forbidden:
        raise v2.WorkerError(
            "Codex V6 patch touches path outside controller allowlist: " + ", ".join(forbidden)
        )
    return tools.apply_patch(patch)


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), Mapping) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), Mapping) else {}
    result: dict[str, int] = {}
    for key, value in (
        ("input_tokens", usage.get("input_tokens")),
        ("cached_tokens", details.get("cached_tokens")),
        ("cache_write_tokens", details.get("cache_write_tokens")),
        ("output_tokens", usage.get("output_tokens")),
        ("reasoning_tokens", output_details.get("reasoning_tokens")),
        ("total_tokens", usage.get("total_tokens")),
    ):
        if type(value) is int and value >= 0:
            result[key] = value
    return result


def _add_usage(total: dict[str, int], current: Mapping[str, int]) -> None:
    for key, value in current.items():
        total[key] = total.get(key, 0) + value


def _response_call(
    *,
    key: str,
    charter: str,
    source: str,
    contract_id: str,
    request: Mapping[str, Any],
    phase: str,
    evidence: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None]:
    prompt = {
        "phase": phase,
        "engineering_request": request,
        "controller_evidence": evidence,
        "instructions": (
            "Use supplied evidence first. Return PATCH immediately when safe. "
            "Return NEED_EVIDENCE only with concrete file:path, test:path, or symbol:path#Symbol requests. "
            "Do not request repository navigation, PR discovery, GitHub state, source SHA discovery, or arbitrary shell. "
            "Return BLOCKED for contradictions or missing capability. Never weaken tests."
        ),
    }
    body = {
        "model": MODEL,
        "instructions": charter,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt, separators=(",", ":"), ensure_ascii=False)}]}],
        "reasoning": {"effort": "high"},
        "text": {"verbosity": "low", "format": {"type": "json_schema", "name": "qore_codex_v6_decision", "strict": True, "schema": RESPONSE_SCHEMA}},
        "max_output_tokens": 7000,
        "store": False,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        "metadata": {
            "qore_role": "principal_engineer_worker_v6",
            "qore_main_sha": source,
            "contract_id": contract_id[:64],
            "phase": phase,
        },
    }
    payload = v2.api_call(key, body)
    if payload.get("status") != "completed":
        raise v2.WorkerError(f"Codex V6 response did not complete: {payload.get('status')}")
    rendered = _output_text(payload)
    try:
        decision = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise v2.WorkerError("Codex V6 structured response is not valid JSON") from exc
    if not isinstance(decision, dict):
        raise v2.WorkerError("Codex V6 structured response is not an object")
    action = decision.get("action")
    patch = decision.get("patch")
    requests = decision.get("evidence_requests")
    if action not in {"PATCH", "NEED_EVIDENCE", "BLOCKED"}:
        raise v2.WorkerError("Codex V6 returned invalid action")
    if not isinstance(patch, str) or not isinstance(requests, list):
        raise v2.WorkerError("Codex V6 response shape is invalid")
    if action == "PATCH" and not patch.strip():
        raise v2.WorkerError("PATCH action requires non-empty patch")
    if action != "PATCH" and patch:
        raise v2.WorkerError("non-PATCH action must not contain patch")
    if action != "NEED_EVIDENCE" and requests:
        raise v2.WorkerError("only NEED_EVIDENCE may contain evidence requests")
    return decision, payload.get("id") if isinstance(payload.get("id"), str) else None


def _qg_failure_evidence(qg: Mapping[str, Any]) -> dict[str, Any]:
    results = qg.get("results")
    bounded: list[dict[str, Any]] = []
    total = 0
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, Mapping):
                continue
            rendered = {
                "command": item.get("command"),
                "returncode": item.get("returncode"),
                "output": str(item.get("output") or "")[:MAX_QG_FAILURE_CHARS],
            }
            size = len(json.dumps(rendered, separators=(",", ":"), ensure_ascii=False))
            if total + size > MAX_QG_FAILURE_CHARS:
                break
            bounded.append(rendered)
            total += size
    return {"quality_gate_success": False, "results": bounded}


def _blocked_result(repo: Path, source: str, contract_id: str, summary: str, notes: Sequence[str], tools: v2.LocalTools, calls: int) -> dict[str, Any]:
    return v2.make_result(repo, source, contract_id, "BLOCKED", summary, list(notes), tools, max(1, calls))


def execute_v6(*, key: str, repo: Path, request: dict[str, Any], charter: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source, contract = v2.validate_request(request, repo)
    contract_id = str(contract.get("contract_id") or "")
    if not contract_id:
        raise v2.WorkerError("contract_id is required")

    reference_sha = v4.required_materialized_reference(contract, source)
    materialization: dict[str, Any] | None = None
    if reference_sha is not None:
        references = v4.v3.contract_reference_shas(contract, source)
        materialization = v4.materialize_reference_delta(repo, source, reference_sha, references)

    tools = v2.LocalTools(repo)
    initial, allowlist = _initial_evidence(repo, contract, materialization)
    usage_total: dict[str, int] = {}
    response_ids: list[str] = []
    phases: list[str] = []
    calls = 0

    decision, response_id = _response_call(
        key=key,
        charter=charter,
        source=source,
        contract_id=contract_id,
        request=request,
        phase="IMPLEMENT",
        evidence=initial,
    )
    calls += 1
    phases.append("IMPLEMENT")
    if response_id:
        response_ids.append(response_id)

    action = decision["action"]
    if action == "BLOCKED":
        final = _blocked_result(repo, source, contract_id, str(decision.get("summary") or "Codex V6 blocked."), decision.get("notes") or [], tools, calls)
    elif action == "NEED_EVIDENCE":
        continuation = resolve_evidence_requests(repo, decision["evidence_requests"])
        second, response_id = _response_call(
            key=key,
            charter=charter,
            source=source,
            contract_id=contract_id,
            request=request,
            phase="EVIDENCE_CONTINUATION",
            evidence={"initial_evidence_digest": hashlib.sha256(json.dumps(initial, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(), "requested_evidence": continuation},
        )
        calls += 1
        phases.append("EVIDENCE_CONTINUATION")
        if response_id:
            response_ids.append(response_id)
        if second["action"] != "PATCH":
            final = _blocked_result(repo, source, contract_id, "Codex V6 exhausted its single deterministic evidence continuation without producing a patch.", second.get("notes") or [], tools, calls)
        else:
            _apply_allowlisted_patch(tools, second["patch"], allowlist=allowlist)
            qg = tools.run_quality_gate()
            if qg.get("success") is True:
                final = v2.make_result(repo, source, contract_id, "READY", str(second.get("summary") or "Codex V6 candidate passed QG."), ["Evidence-first V6 used at most two model calls.", "READY is engineering-only; semantic/reviewer/final Sol gates remain mandatory."], tools, calls)
            else:
                final = _blocked_result(repo, source, contract_id, "Codex V6 used its only continuation before Quality Gate; failed QG requires a fresh bounded engineering task.", ["No third model call is permitted."], tools, calls)
    else:
        _apply_allowlisted_patch(tools, decision["patch"], allowlist=allowlist)
        qg = tools.run_quality_gate()
        if qg.get("success") is True:
            final = v2.make_result(repo, source, contract_id, "READY", str(decision.get("summary") or "Codex V6 candidate passed QG."), ["Evidence-first V6 completed in one model call.", "READY is engineering-only; semantic/reviewer/final Sol gates remain mandatory."], tools, calls)
        else:
            second, response_id = _response_call(
                key=key,
                charter=charter,
                source=source,
                contract_id=contract_id,
                request=request,
                phase="QG_CORRECTION",
                evidence={"candidate_diff": _candidate_diff(repo), "qg_failure": _qg_failure_evidence(qg)},
            )
            calls += 1
            phases.append("QG_CORRECTION")
            if response_id:
                response_ids.append(response_id)
            if second["action"] != "PATCH":
                final = _blocked_result(repo, source, contract_id, "Codex V6 correction call did not produce a patch.", second.get("notes") or [], tools, calls)
            else:
                _apply_allowlisted_patch(tools, second["patch"], allowlist=allowlist)
                qg2 = tools.run_quality_gate()
                if qg2.get("success") is True:
                    final = v2.make_result(repo, source, contract_id, "READY", str(second.get("summary") or "Codex V6 corrected candidate passed QG."), ["One implementation call plus one QG-correction call.", "No third model call was made."], tools, calls)
                else:
                    final = _blocked_result(repo, source, contract_id, "Codex V6 second and final model call did not produce a green Quality Gate.", ["No third model call is permitted."], tools, calls)

    usage = {
        "model": MODEL,
        "worker_version": WORKER_VERSION,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        "model_calls": calls,
        "max_model_calls": MAX_MODEL_CALLS,
        "max_total_tokens": MAX_TOTAL_TOKENS,
        "phases": phases,
        "response_ids": response_ids,
        "reference_materialized": reference_sha is not None,
        "materialized_reference_sha": reference_sha,
        "exploration_turns": 0,
        "hidden_retries": 0,
        "production_authority": False,
    }
    usage.update(usage_total)
    return final, usage


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
    if not isinstance(request, dict):
        raise v2.WorkerError("engineering request is not an object")
    charter = Path(args.charter).read_text(encoding="utf-8")
    final, usage = execute_v6(key=key, repo=repo, request=request, charter=charter)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.usage_output).write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_ENGINEER_WORKER_V6_OK status={final['status']} model_calls={usage['model_calls']} exploration_turns=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except v2.WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_V6_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8) from exc
