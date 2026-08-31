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
PATH_RE = re.compile(
    r"(?P<path>(?:src|tests|docs|schemas|scripts|charters)/[A-Za-z0-9_.@/+\-]+)"
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


def _response_call(
    key: str,
    *,
    charter: str,
    source: str,
    contract_id: str,
    payload: Mapping[str, Any],
    usage_total: dict[str, int],
    phase: str,
) -> tuple[dict[str, Any], str | None]:
    response = v2.api_call(
        key,
        {
            "model": MODEL,
            "instructions": charter,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "QORE deterministic-first engineering capsule. Repository navigation, reference "
                                "materialization, and supplied evidence have already been performed by the controller. "
                                "Do not ask to rediscover supplied facts. Return PATCH only if the bounded evidence is "
                                "sufficient to implement the exact contract safely. If one concrete file/symbol/test is "
                                "missing, return NEED_EVIDENCE using only file:path, test:path, or symbol:path#Symbol. "
                                "Never weaken tests, validation, security boundaries, or Production restrictions.\n\n"
                                + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                            ),
                        }
                    ],
                }
            ],
            "reasoning": {"effort": "high"},
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "qore_codex_v6_action",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            },
            "max_output_tokens": 12_000,
            "store": False,
            "prompt_cache_key": PROMPT_CACHE_KEY,
            "metadata": {
                "qore_role": "principal_engineer_worker_v6",
                "qore_main_sha": source,
                "contract_id": contract_id[:64],
                "phase": phase,
            },
        },
    )
    if response.get("status") != "completed":
        raise v2.WorkerError(f"Codex V6 response did not complete: {response.get('status')}")
    v2.add_usage(usage_total, response)
    if v2.spend_equivalent_tokens(usage_total) > MAX_TOTAL_TOKENS:
        raise v2.WorkerError("Codex V6 exceeded the hard spend-equivalent token budget")
    rendered = _output_text(response)
    try:
        action = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise v2.WorkerError("Codex V6 structured response is invalid JSON") from exc
    if not isinstance(action, dict):
        raise v2.WorkerError("Codex V6 structured response is not an object")
    response_id = response.get("id") if isinstance(response.get("id"), str) else None
    return action, response_id


def _validate_action(action: Mapping[str, Any]) -> tuple[str, str, list[str], str, list[str]]:
    kind = action.get("action")
    patch = action.get("patch")
    requests = action.get("evidence_requests")
    summary = action.get("summary")
    notes = action.get("notes")
    if kind not in {"PATCH", "NEED_EVIDENCE", "BLOCKED"}:
        raise v2.WorkerError("Codex V6 action is invalid")
    if not isinstance(patch, str) or not isinstance(requests, list) or not isinstance(summary, str) or not isinstance(notes, list):
        raise v2.WorkerError("Codex V6 response fields are invalid")
    if any(not isinstance(item, str) for item in requests + notes):
        raise v2.WorkerError("Codex V6 response lists must contain strings")
    if kind == "PATCH" and not patch.strip():
        raise v2.WorkerError("PATCH action requires a patch")
    if kind != "PATCH" and patch:
        raise v2.WorkerError("non-PATCH action must not contain a patch")
    if kind == "NEED_EVIDENCE" and not requests:
        raise v2.WorkerError("NEED_EVIDENCE requires concrete requests")
    if kind != "NEED_EVIDENCE" and requests:
        raise v2.WorkerError("only NEED_EVIDENCE may request evidence")
    return kind, patch, [str(x) for x in requests], summary, [str(x) for x in notes]


def _apply_allowlisted_patch(
    tools: v2.LocalTools,
    patch: str,
    *,
    allowlist: Sequence[str],
) -> dict[str, Any]:
    paths = v2.validate_patch_paths(patch)
    allowed = set(allowlist)
    if not allowed:
        raise v2.WorkerError("controller has no changed-file allowlist for model patch")
    outside = sorted(set(paths) - allowed)
    if outside:
        raise v2.WorkerError("model patch touches paths outside controller allowlist: " + ", ".join(outside))
    result = tools.apply_patch(patch)
    if result.get("applied") is not True:
        raise v2.WorkerError("model patch failed deterministic git-apply preflight")
    return result


def _qg_failure_evidence(qg: Mapping[str, Any]) -> dict[str, Any]:
    bounded: list[dict[str, Any]] = []
    results = qg.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, Mapping):
                continue
            output = str(item.get("output") or "")
            bounded.append(
                {
                    "command": item.get("command"),
                    "returncode": item.get("returncode"),
                    "output": output[-MAX_QG_FAILURE_CHARS:],
                }
            )
    return {"success": qg.get("success") is True, "results": bounded}


def execute_v6(
    *,
    key: str,
    repo: Path,
    request: dict[str, Any],
    charter: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source, contract = v2.validate_request(request, repo)
    contract_id = str(contract.get("contract_id") or "")
    if not contract_id:
        raise v2.WorkerError("contract_id is required")

    reference_sha = v4.required_materialized_reference(contract, source)
    materialization: dict[str, Any] | None = None
    if reference_sha is not None:
        allowed_references = tuple({reference_sha})
        materialization = v4.materialize_reference_delta(
            repo,
            source,
            reference_sha,
            allowed_references,
        )

    evidence, allowlist = _initial_evidence(repo, contract, materialization)
    if not allowlist:
        raise v2.WorkerError(
            "Codex V6 requires deterministic changed-file allowlist from contract paths or exact reference delta"
        )
    tools = v2.LocalTools(repo)
    usage_total: dict[str, int] = {}
    response_ids: list[str] = []
    call_count = 0

    base_capsule: dict[str, Any] = {
        "schema_version": "qore.codex.v6.evidence.capsule.v1",
        "source_main_sha": source,
        "contract": contract,
        "changed_file_allowlist": list(allowlist),
        "forbidden": list(contract.get("forbidden") or []),
        "evidence": evidence,
        "missing_evidence_protocol": "file:path | test:path | symbol:path#Symbol",
        "max_model_calls": MAX_MODEL_CALLS,
        "production_authority": False,
    }

    action, response_id = _response_call(
        key,
        charter=charter,
        source=source,
        contract_id=contract_id,
        payload=base_capsule,
        usage_total=usage_total,
        phase="initial",
    )
    call_count += 1
    if response_id:
        response_ids.append(response_id)
    kind, patch, requests, summary, notes = _validate_action(action)

    if kind == "BLOCKED":
        final = v2.make_result(repo, source, contract_id, "BLOCKED", summary, notes, tools, call_count)
    else:
        if kind == "NEED_EVIDENCE":
            requested = resolve_evidence_requests(repo, requests)
            followup = {
                **base_capsule,
                "continuation_reason": "deterministic_evidence_requested",
                "requested_evidence": requested,
                "current_diff": _candidate_diff(repo),
                "previous_summary": summary,
            }
            action, response_id = _response_call(
                key,
                charter=charter,
                source=source,
                contract_id=contract_id,
                payload=followup,
                usage_total=usage_total,
                phase="evidence_continuation",
            )
            call_count += 1
            if response_id:
                response_ids.append(response_id)
            kind, patch, requests, summary, notes = _validate_action(action)
            if kind == "NEED_EVIDENCE":
                final = v2.make_result(
                    repo,
                    source,
                    contract_id,
                    "BLOCKED",
                    "Codex V6 exhausted its single deterministic evidence continuation.",
                    notes + ["No hidden retry or third model call is permitted."],
                    tools,
                    call_count,
                )
            elif kind == "BLOCKED":
                final = v2.make_result(repo, source, contract_id, "BLOCKED", summary, notes, tools, call_count)
            else:
                _apply_allowlisted_patch(tools, patch, allowlist=allowlist)
                qg = tools.run_quality_gate()
                if qg.get("success") is True:
                    final = v2.make_result(
                        repo,
                        source,
                        contract_id,
                        "READY",
                        summary,
                        notes + ["V6 deterministic full Quality Gate passed after one evidence continuation."],
                        tools,
                        call_count,
                    )
                else:
                    final = v2.make_result(
                        repo,
                        source,
                        contract_id,
                        "BLOCKED",
                        "Codex V6 patch failed the full Quality Gate after the evidence continuation.",
                        notes + ["A third model call is forbidden."],
                        tools,
                        call_count,
                    )
        else:
            _apply_allowlisted_patch(tools, patch, allowlist=allowlist)
            qg = tools.run_quality_gate()
            if qg.get("success") is True:
                final = v2.make_result(
                    repo,
                    source,
                    contract_id,
                    "READY",
                    summary,
                    notes + ["V6 deterministic full Quality Gate passed after the initial patch."],
                    tools,
                    call_count,
                )
            else:
                if call_count >= MAX_MODEL_CALLS:
                    final = v2.make_result(
                        repo,
                        source,
                        contract_id,
                        "BLOCKED",
                        "Codex V6 full Quality Gate failed and no correction call remains.",
                        notes,
                        tools,
                        call_count,
                    )
                else:
                    correction_capsule = {
                        **base_capsule,
                        "continuation_reason": "quality_gate_correction",
                        "current_diff": _candidate_diff(repo),
                        "quality_gate_failure": _qg_failure_evidence(qg),
                        "instruction": (
                            "Return one smallest correction PATCH for the existing candidate. Do not revert valid "
                            "reference materialization or broaden scope."
                        ),
                    }
                    action, response_id = _response_call(
                        key,
                        charter=charter,
                        source=source,
                        contract_id=contract_id,
                        payload=correction_capsule,
                        usage_total=usage_total,
                        phase="qg_correction",
                    )
                    call_count += 1
                    if response_id:
                        response_ids.append(response_id)
                    kind, patch, requests, correction_summary, correction_notes = _validate_action(action)
                    if kind != "PATCH":
                        final = v2.make_result(
                            repo,
                            source,
                            contract_id,
                            "BLOCKED",
                            correction_summary,
                            correction_notes + ["V6 correction continuation did not return a patch."],
                            tools,
                            call_count,
                        )
                    else:
                        _apply_allowlisted_patch(tools, patch, allowlist=allowlist)
                        qg2 = tools.run_quality_gate()
                        if qg2.get("success") is True:
                            final = v2.make_result(
                                repo,
                                source,
                                contract_id,
                                "READY",
                                correction_summary,
                                correction_notes + ["V6 deterministic full Quality Gate passed after one correction."],
                                tools,
                                call_count,
                            )
                        else:
                            final = v2.make_result(
                                repo,
                                source,
                                contract_id,
                                "BLOCKED",
                                "Codex V6 correction failed the full Quality Gate.",
                                correction_notes + ["No hidden retry or third model call is permitted."],
                                tools,
                                call_count,
                            )

    usage = {
        **usage_total,
        "model": MODEL,
        "worker_version": WORKER_VERSION,
        "prompt_cache_key": PROMPT_CACHE_KEY,
        "response_ids": response_ids,
        "model_calls": call_count,
        "max_model_calls": MAX_MODEL_CALLS,
        "max_total_tokens": MAX_TOTAL_TOKENS,
        "budget_formula_version": v2.BUDGET_FORMULA_VERSION,
        "budget_tokens": v2.spend_equivalent_tokens(usage_total),
        "reference_sha": reference_sha,
        "materialization_evidence": materialization,
        "changed_file_allowlist": list(allowlist),
        "deterministic_evidence_first": True,
        "hidden_retries": 0,
        "production_authority": False,
    }
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
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    usage_path = Path(args.usage_output)
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    usage_path.write_text(json.dumps(usage, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "CODEX_ENGINEER_WORKER_V6_OK status={} main={} calls={} changed_files={}".format(
            final["status"], final["source_main_sha"], usage["model_calls"], len(final["changed_files"])
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (v2.WorkerError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"CODEX_ENGINEER_WORKER_V6_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(8)
