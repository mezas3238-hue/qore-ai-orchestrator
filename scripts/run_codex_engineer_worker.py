#!/usr/bin/env python3
"""Run GPT-5.3-Codex as a bounded local engineering worker.

The model can inspect and patch a local checkout through narrowly-scoped function
tools. It never receives a GitHub write token. Publication is a separate controller
step after an independent deterministic Quality Gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ENDPOINT = "https://api.openai.com/v1/responses"
MODEL = "gpt-5.3-codex"
PROMPT_CACHE_KEY = "qore-codex-engineer-worker-v1"
MAX_TURNS = 32
MAX_PATCH_CHARS = 120_000
MAX_TOOL_OUTPUT_CHARS = 24_000
MAX_CHANGED_FILES = 30
MAX_READ_LINES = 1200
QUALITY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("ruff", "check", "."),
    ("mypy", "src", "tests"),
    ("pytest", "--cov=src/qore", "--cov-report=term-missing"),
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class WorkerError(RuntimeError):
    pass


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    keep = max(1000, limit - 100)
    return "[...truncated...]\n" + text[-keep:]


def _run(
    args: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    result = _run(["git", *args], cwd=repo, timeout=timeout)
    if result.returncode != 0:
        raise WorkerError(f"git {' '.join(args)} failed: {_clip(result.stdout, 4000)}")
    return result.stdout


def _safe_path(repo: Path, raw: str, *, must_exist: bool = True) -> Path:
    if not raw or "\x00" in raw:
        raise WorkerError("path is empty or invalid")
    candidate = (repo / raw).resolve()
    root = repo.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkerError("path escapes repository") from exc
    rel = candidate.relative_to(root).as_posix()
    if rel == ".git" or rel.startswith(".git/"):
        raise WorkerError(".git access is forbidden")
    if must_exist and not candidate.exists():
        raise WorkerError(f"path does not exist: {raw}")
    return candidate


def _tracked_and_untracked(repo: Path) -> list[str]:
    tracked = _git(repo, "ls-files", "-co", "--exclude-standard").splitlines()
    return sorted({item for item in tracked if item and not item.startswith(".git/")})


def _changed_files(repo: Path) -> list[str]:
    names = _git(repo, "diff", "--name-only", "--").splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({item for item in names + untracked if item})


def _validate_patch_paths(patch: str) -> list[str]:
    if not patch or len(patch) > MAX_PATCH_CHARS:
        raise WorkerError("patch is empty or exceeds the hard size bound")
    if "120000" in patch or "160000" in patch:
        raise WorkerError("symlink/submodule mode changes are forbidden")
    paths: set[str] = set()
    for line in patch.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        token = line[4:].split("\t", 1)[0].strip()
        if token == "/dev/null":
            continue
        if token.startswith("a/") or token.startswith("b/"):
            token = token[2:]
        pure = Path(token)
        if pure.is_absolute() or ".." in pure.parts or not token:
            raise WorkerError(f"unsafe patch path: {token}")
        if pure.parts and pure.parts[0] == ".git":
            raise WorkerError("patch may not touch .git")
        paths.add(pure.as_posix())
    if not paths:
        raise WorkerError("patch contains no file paths")
    if len(paths) > MAX_CHANGED_FILES:
        raise WorkerError("patch changes too many files")
    return sorted(paths)


def _command_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "output": _clip(result.stdout),
    }


class LocalTools:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.last_quality_success = False
        self.quality_runs = 0

    def list_files(self, prefix: str, limit: int) -> dict[str, Any]:
        if limit < 1 or limit > 500:
            raise WorkerError("limit must be 1..500")
        prefix = prefix.strip().lstrip("./")
        files = [p for p in _tracked_and_untracked(self.repo) if not prefix or p.startswith(prefix)]
        return {"files": files[:limit], "truncated": len(files) > limit}

    def read_file(self, path: str, start_line: int, end_line: int) -> dict[str, Any]:
        if start_line < 1 or end_line < start_line or end_line - start_line + 1 > MAX_READ_LINES:
            raise WorkerError(f"line range must be positive and <= {MAX_READ_LINES} lines")
        target = _safe_path(self.repo, path)
        if not target.is_file() or target.is_symlink():
            raise WorkerError("read_file requires a regular non-symlink file")
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkerError("binary/non-UTF8 file cannot be read") from exc
        lines = text.splitlines()
        selected = lines[start_line - 1 : end_line]
        rendered = "\n".join(f"{start_line + i}: {line}" for i, line in enumerate(selected))
        return {"path": path, "total_lines": len(lines), "content": _clip(rendered)}

    def search_text(self, query: str, prefix: str, max_results: int) -> dict[str, Any]:
        if not query or len(query) > 300:
            raise WorkerError("query must be 1..300 characters")
        if max_results < 1 or max_results > 200:
            raise WorkerError("max_results must be 1..200")
        prefix = prefix.strip().lstrip("./")
        hits: list[dict[str, Any]] = []
        for rel in _tracked_and_untracked(self.repo):
            if prefix and not rel.startswith(prefix):
                continue
            path = _safe_path(self.repo, rel)
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_500_000:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, start=1):
                if query in line:
                    hits.append({"path": rel, "line": number, "text": line[:500]})
                    if len(hits) >= max_results:
                        return {"hits": hits, "truncated": True}
        return {"hits": hits, "truncated": False}

    def apply_patch(self, patch: str) -> dict[str, Any]:
        paths = _validate_patch_paths(patch)
        for path in paths:
            _safe_path(self.repo, path, must_exist=False)
        check = _run(
            ["git", "apply", "--check", "--whitespace=error-all", "-"],
            cwd=self.repo,
            input_text=patch,
            timeout=60,
        )
        if check.returncode != 0:
            return {"applied": False, "error": _clip(check.stdout, 8000)}
        applied = _run(["git", "apply", "--whitespace=error-all", "-"], cwd=self.repo, input_text=patch, timeout=60)
        if applied.returncode != 0:
            raise WorkerError(f"git apply failed after successful check: {_clip(applied.stdout, 8000)}")
        changed = _changed_files(self.repo)
        if len(changed) > MAX_CHANGED_FILES:
            raise WorkerError("working tree exceeds changed-file hard bound")
        self.last_quality_success = False
        return {"applied": True, "patch_paths": paths, "changed_files": changed}

    def git_diff(self, max_chars: int) -> dict[str, Any]:
        if max_chars < 1000 or max_chars > 60_000:
            raise WorkerError("max_chars must be 1000..60000")
        diff = _git(self.repo, "diff", "--", timeout=60)
        untracked: dict[str, str] = {}
        for rel in _git(self.repo, "ls-files", "--others", "--exclude-standard").splitlines():
            path = _safe_path(self.repo, rel)
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 100_000:
                try:
                    untracked[rel] = path.read_text(encoding="utf-8")[:20_000]
                except UnicodeDecodeError:
                    untracked[rel] = "<binary omitted>"
        rendered = diff + ("\nUNTRACKED:\n" + json.dumps(untracked, ensure_ascii=False) if untracked else "")
        return {"changed_files": _changed_files(self.repo), "diff": _clip(rendered, max_chars)}

    def run_targeted_pytest(self, path: str) -> dict[str, Any]:
        target = _safe_path(self.repo, path)
        rel = target.relative_to(self.repo.resolve()).as_posix()
        if not (rel == "tests" or rel.startswith("tests/")):
            raise WorkerError("targeted pytest is restricted to tests/")
        result = _run(["pytest", "-q", rel], cwd=self.repo, timeout=240)
        return _command_result(result)

    def run_quality_gate(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        success = True
        for command in QUALITY_COMMANDS:
            result = _run(list(command), cwd=self.repo, timeout=600)
            results.append({"command": " ".join(command), **_command_result(result)})
            if result.returncode != 0:
                success = False
                break
        self.quality_runs += 1
        self.last_quality_success = success
        return {"success": success, "run_number": self.quality_runs, "results": results}


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List tracked/untracked non-ignored repository files under an optional prefix.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"prefix": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["prefix", "limit"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a bounded UTF-8 line range from a repository file.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path", "start_line", "end_line"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_text",
        "description": "Search literal text in bounded repository files.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string"},
                "prefix": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query", "prefix", "max_results"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": "Apply a bounded unified git diff to the local checkout after controller validation.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "git_diff",
        "description": "Inspect the current local candidate diff and changed-file list.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"max_chars": {"type": "integer"}},
            "required": ["max_chars"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_targeted_pytest",
        "description": "Run pytest on one tests/ path only; no arbitrary shell command is accepted.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_quality_gate",
        "description": "Run the immutable QORE full gate: ruff, mypy, then pytest with coverage.",
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        "strict": True,
    },
    {
        "type": "function",
        "name": "finish",
        "description": "Finish only when the contract is READY with a green full gate, or BLOCKED with a concrete reason.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["READY", "BLOCKED"]},
                "summary": {"type": "string"},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["status", "summary", "notes"],
        },
        "strict": True,
    },
]


def _api_call(key: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=420) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WorkerError(f"OpenAI Codex request failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise WorkerError(f"OpenAI Codex request failed: {type(exc).__name__}") from exc


def _validate_request(request: dict[str, Any], repo: Path) -> tuple[str, dict[str, Any]]:
    if request.get("schema_version") != "qore.codex.engineering.request.v1":
        raise WorkerError("unexpected Codex engineering request schema")
    source = request.get("source_main_sha")
    if not isinstance(source, str) or not SHA_RE.fullmatch(source):
        raise WorkerError("source_main_sha is invalid")
    contract = request.get("engineering_contract")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        raise WorkerError("enabled engineering contract is required")
    if contract.get("target_repository") != "mezas3238-hue/qore-core":
        raise WorkerError("this worker version is restricted to qore-core")
    if _git(repo, "rev-parse", "HEAD").strip() != source:
        raise WorkerError("local checkout is not bound to source_main_sha")
    if _git(repo, "status", "--porcelain").strip():
        raise WorkerError("local checkout must start clean")
    return source, contract


def _tool_call(tools: LocalTools, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "list_files":
        return tools.list_files(str(arguments["prefix"]), int(arguments["limit"]))
    if name == "read_file":
        return tools.read_file(str(arguments["path"]), int(arguments["start_line"]), int(arguments["end_line"]))
    if name == "search_text":
        return tools.search_text(str(arguments["query"]), str(arguments["prefix"]), int(arguments["max_results"]))
    if name == "apply_patch":
        return tools.apply_patch(str(arguments["patch"]))
    if name == "git_diff":
        return tools.git_diff(int(arguments["max_chars"]))
    if name == "run_targeted_pytest":
        return tools.run_targeted_pytest(str(arguments["path"]))
    if name == "run_quality_gate":
        return tools.run_quality_gate()
    raise WorkerError(f"unsupported tool: {name}")


def _usage_add(total: dict[str, int], payload: dict[str, Any]) -> None:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    details_in = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    details_out = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    for key, value in (
        ("input_tokens", usage.get("input_tokens")),
        ("cached_tokens", details_in.get("cached_tokens")),
        ("cache_write_tokens", details_in.get("cache_write_tokens")),
        ("output_tokens", usage.get("output_tokens")),
        ("reasoning_tokens", details_out.get("reasoning_tokens")),
        ("total_tokens", usage.get("total_tokens")),
    ):
        if type(value) is int:
            total[key] = total.get(key, 0) + value


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
    source, contract = _validate_request(request, repo)
    charter = Path(args.charter).read_text(encoding="utf-8")
    local_tools = LocalTools(repo)

    prompt = (
        "Execute the architect-issued engineering contract against the exact local checkout. "
        "You are a real bounded engineering worker, not a plan generator. Inspect before editing. "
        "Use apply_patch for all modifications. You have no arbitrary shell, network, GitHub credential, "
        "merge authority, reviewer authority, or Production authority. Run targeted tests as useful and "
        "run_quality_gate before finish READY. Never weaken tests or validation. If the contract cannot be "
        "safely completed within this scope, finish BLOCKED.\n\nENGINEERING_REQUEST:\n"
        + json.dumps(request, separators=(",", ":"), ensure_ascii=False)
    )
    conversation: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
    ]
    usage_total: dict[str, int] = {}
    final: dict[str, Any] | None = None
    response_ids: list[str] = []

    for turn in range(1, MAX_TURNS + 1):
        body = {
            "model": MODEL,
            "instructions": charter,
            "input": conversation,
            "tools": TOOLS,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "reasoning": {"effort": "high"},
            "max_output_tokens": 7000,
            "store": False,
            "prompt_cache_key": PROMPT_CACHE_KEY,
            "metadata": {
                "qore_role": "principal_engineer_worker",
                "qore_main_sha": source,
                "contract_id": str(contract.get("contract_id") or "")[:64],
            },
        }
        payload = _api_call(key, body)
        if payload.get("status") != "completed":
            raise WorkerError(f"Codex response did not complete: {payload.get('status')}")
        if isinstance(payload.get("id"), str):
            response_ids.append(payload["id"])
        _usage_add(usage_total, payload)
        outputs = payload.get("output")
        if not isinstance(outputs, list):
            raise WorkerError("Codex response output is invalid")
        function_calls = [item for item in outputs if isinstance(item, dict) and item.get("type") == "function_call"]
        if len(function_calls) != 1:
            raise WorkerError(f"expected exactly one function call, got {len(function_calls)}")
        call = function_calls[0]
        name = call.get("name")
        call_id = call.get("call_id")
        raw_arguments = call.get("arguments")
        if not isinstance(name, str) or not isinstance(call_id, str) or not isinstance(raw_arguments, str):
            raise WorkerError("function call shape is invalid")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise WorkerError("function call arguments are invalid JSON") from exc
        if not isinstance(arguments, dict):
            raise WorkerError("function call arguments must be an object")

        conversation.extend(item for item in outputs if isinstance(item, dict))

        if name == "finish":
            status = arguments.get("status")
            summary = arguments.get("summary")
            notes = arguments.get("notes")
            if status not in {"READY", "BLOCKED"} or not isinstance(summary, str) or not isinstance(notes, list):
                raise WorkerError("finish arguments are invalid")
            changed = _changed_files(repo)
            if status == "READY":
                if not changed:
                    raise WorkerError("READY requires a non-empty candidate diff")
                if not local_tools.last_quality_success:
                    raise WorkerError("READY requires a successful run_quality_gate after the last patch")
            final = {
                "schema_version": "qore.codex.worker.result.v1",
                "source_main_sha": source,
                "contract_id": contract.get("contract_id"),
                "status": status,
                "summary": summary,
                "notes": [str(item) for item in notes],
                "changed_files": changed,
                "quality_gate_success": local_tools.last_quality_success,
                "quality_gate_runs": local_tools.quality_runs,
                "diff_sha256": hashlib.sha256(_git(repo, "diff", "--").encode("utf-8")).hexdigest(),
                "turns": turn,
                "production_authority": False,
            }
            break

        try:
            result = _tool_call(local_tools, name, arguments)
            tool_output = {"ok": True, "result": result}
        except (WorkerError, OSError, subprocess.TimeoutExpired) as exc:
            tool_output = {"ok": False, "error": str(exc)}
        conversation.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": _clip(json.dumps(tool_output, ensure_ascii=False), MAX_TOOL_OUTPUT_CHARS),
            }
        )

    if final is None:
        raise WorkerError(f"Codex worker exceeded {MAX_TURNS} turns without finish")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    usage_total.update(
        {
            "model": MODEL,
            "prompt_cache_key": PROMPT_CACHE_KEY,
            "response_ids": response_ids,
            "turns": final["turns"],
        }
    )
    Path(args.usage_output).write_text(json.dumps(usage_total, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "CODEX_ENGINEER_WORKER_OK status={} main={} contract={} changed_files={}".format(
            final["status"], source, final["contract_id"], len(final["changed_files"])
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(7) from exc
