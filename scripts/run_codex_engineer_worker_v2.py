#!/usr/bin/env python3
"""Bounded GPT-5.3-Codex engineering worker for an exact qore-core checkout.

The worker receives no GitHub credential and no arbitrary shell tool. All local
operations are controller-defined functions. Publication is handled separately.
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
PROMPT_CACHE_KEY = "qore-codex-engineer-worker-v2"
MAX_TURNS = 16
MAX_TOTAL_TOKENS = 120_000
MAX_PATCH_CHARS = 120_000
MAX_TOOL_OUTPUT_CHARS = 24_000
MAX_CHANGED_FILES = 30
MAX_READ_LINES = 1200
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
QUALITY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("ruff", "check", "."),
    ("mypy", "src", "tests"),
    ("pytest", "--cov=src/qore", "--cov-report=term-missing"),
)


class WorkerError(RuntimeError):
    pass


def clip(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return "[...truncated...]\n" + text[-max(1000, limit - 100):]


def run_process(
    args: list[str], *, cwd: Path, input_text: str | None = None, timeout: int = 300
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


def git(repo: Path, *args: str, timeout: int = 60) -> str:
    result = run_process(["git", *args], cwd=repo, timeout=timeout)
    if result.returncode != 0:
        raise WorkerError(f"git {' '.join(args)} failed: {clip(result.stdout, 4000)}")
    return result.stdout


def safe_path(repo: Path, raw: str, *, must_exist: bool = True) -> Path:
    if not raw or "\x00" in raw:
        raise WorkerError("path is empty or invalid")
    root = repo.resolve()
    candidate = (root / raw).resolve()
    try:
        rel = candidate.relative_to(root)
    except ValueError as exc:
        raise WorkerError("path escapes repository") from exc
    rel_text = rel.as_posix()
    if rel_text == ".git" or rel_text.startswith(".git/"):
        raise WorkerError(".git access is forbidden")
    if must_exist and not candidate.exists():
        raise WorkerError(f"path does not exist: {raw}")
    return candidate


def all_files(repo: Path) -> list[str]:
    values = git(repo, "ls-files", "-co", "--exclude-standard").splitlines()
    return sorted({value for value in values if value and not value.startswith(".git/")})


def changed_files(repo: Path) -> list[str]:
    modified = git(repo, "diff", "--name-only", "--").splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted({value for value in modified + untracked if value})


def candidate_fingerprint(repo: Path) -> str:
    """Hash tracked diff plus exact bytes of every untracked candidate file."""
    digest = hashlib.sha256()
    digest.update(b"QORE-CODEX-CANDIDATE-V2\0")
    digest.update(git(repo, "diff", "--binary", "--no-ext-diff", "--").encode("utf-8"))
    for rel in git(repo, "ls-files", "--others", "--exclude-standard").splitlines():
        path = safe_path(repo, rel)
        if not path.is_file() or path.is_symlink():
            raise WorkerError(f"untracked candidate must be a regular non-symlink file: {rel}")
        digest.update(b"\0UNTRACKED\0")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_patch_paths(patch: str) -> list[str]:
    if not patch or len(patch) > MAX_PATCH_CHARS:
        raise WorkerError("patch is empty or exceeds the hard size bound")
    # 120000 = symlink, 160000 = gitlink/submodule.
    if "120000" in patch or "160000" in patch:
        raise WorkerError("symlink/submodule mode changes are forbidden")
    paths: set[str] = set()
    for line in patch.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        token = line[4:].split("\t", 1)[0].strip()
        if token == "/dev/null":
            continue
        if token.startswith(("a/", "b/")):
            token = token[2:]
        pure = Path(token)
        if not token or pure.is_absolute() or ".." in pure.parts:
            raise WorkerError(f"unsafe patch path: {token}")
        if pure.parts and pure.parts[0] == ".git":
            raise WorkerError("patch may not touch .git")
        paths.add(pure.as_posix())
    if not paths:
        raise WorkerError("patch contains no file paths")
    if len(paths) > MAX_CHANGED_FILES:
        raise WorkerError("patch changes too many files")
    return sorted(paths)


def command_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {"returncode": result.returncode, "output": clip(result.stdout)}


class LocalTools:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.last_quality_success = False
        self.quality_runs = 0

    def list_files(self, prefix: str, limit: int) -> dict[str, Any]:
        if not 1 <= limit <= 500:
            raise WorkerError("limit must be 1..500")
        prefix = prefix.strip().lstrip("./")
        matches = [path for path in all_files(self.repo) if not prefix or path.startswith(prefix)]
        return {"files": matches[:limit], "truncated": len(matches) > limit}

    def read_file(self, path: str, start_line: int, end_line: int) -> dict[str, Any]:
        if start_line < 1 or end_line < start_line or end_line - start_line + 1 > MAX_READ_LINES:
            raise WorkerError(f"line range must be positive and <= {MAX_READ_LINES} lines")
        target = safe_path(self.repo, path)
        if not target.is_file() or target.is_symlink():
            raise WorkerError("read_file requires a regular non-symlink file")
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise WorkerError("binary/non-UTF8 file cannot be read") from exc
        selected = lines[start_line - 1 : end_line]
        rendered = "\n".join(f"{start_line + index}: {line}" for index, line in enumerate(selected))
        return {"path": path, "total_lines": len(lines), "content": clip(rendered)}

    def search_text(self, query: str, prefix: str, max_results: int) -> dict[str, Any]:
        if not 1 <= len(query) <= 300:
            raise WorkerError("query must be 1..300 characters")
        if not 1 <= max_results <= 200:
            raise WorkerError("max_results must be 1..200")
        prefix = prefix.strip().lstrip("./")
        hits: list[dict[str, Any]] = []
        for rel in all_files(self.repo):
            if prefix and not rel.startswith(prefix):
                continue
            path = safe_path(self.repo, rel)
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
        paths = validate_patch_paths(patch)
        for path in paths:
            safe_path(self.repo, path, must_exist=False)
        check = run_process(
            ["git", "apply", "--check", "--whitespace=error-all", "-"],
            cwd=self.repo,
            input_text=patch,
            timeout=60,
        )
        if check.returncode != 0:
            return {"applied": False, "error": clip(check.stdout, 8000)}
        applied = run_process(
            ["git", "apply", "--whitespace=error-all", "-"],
            cwd=self.repo,
            input_text=patch,
            timeout=60,
        )
        if applied.returncode != 0:
            raise WorkerError(f"git apply failed after check: {clip(applied.stdout, 8000)}")
        changed = changed_files(self.repo)
        if len(changed) > MAX_CHANGED_FILES:
            raise WorkerError("working tree exceeds changed-file hard bound")
        self.last_quality_success = False
        return {"applied": True, "patch_paths": paths, "changed_files": changed}

    def git_diff(self, max_chars: int) -> dict[str, Any]:
        if not 1000 <= max_chars <= 60_000:
            raise WorkerError("max_chars must be 1000..60000")
        tracked = git(self.repo, "diff", "--", timeout=60)
        untracked: list[dict[str, Any]] = []
        for rel in git(self.repo, "ls-files", "--others", "--exclude-standard").splitlines():
            path = safe_path(self.repo, rel)
            item: dict[str, Any] = {"path": rel, "size": path.stat().st_size}
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 100_000:
                try:
                    item["content"] = path.read_text(encoding="utf-8")[:20_000]
                except UnicodeDecodeError:
                    item["content"] = "<binary omitted>"
            untracked.append(item)
        rendered = tracked + ("\nUNTRACKED:\n" + json.dumps(untracked, ensure_ascii=False) if untracked else "")
        return {"changed_files": changed_files(self.repo), "diff": clip(rendered, max_chars)}

    def run_targeted_pytest(self, path: str) -> dict[str, Any]:
        target = safe_path(self.repo, path)
        rel = target.relative_to(self.repo.resolve()).as_posix()
        if rel != "tests" and not rel.startswith("tests/"):
            raise WorkerError("targeted pytest is restricted to tests/")
        return command_result(run_process(["pytest", "-q", rel], cwd=self.repo, timeout=240))

    def run_quality_gate(self) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        success = True
        for command in QUALITY_COMMANDS:
            result = run_process(list(command), cwd=self.repo, timeout=600)
            results.append({"command": " ".join(command), **command_result(result)})
            if result.returncode != 0:
                success = False
                break
        self.quality_runs += 1
        self.last_quality_success = success
        return {"success": success, "run_number": self.quality_runs, "results": results}


TOOLS: list[dict[str, Any]] = [
    {"type":"function","name":"list_files","description":"List repository files under an optional prefix.","parameters":{"type":"object","additionalProperties":False,"properties":{"prefix":{"type":"string"},"limit":{"type":"integer"}},"required":["prefix","limit"]},"strict":True},
    {"type":"function","name":"read_file","description":"Read a bounded UTF-8 line range.","parameters":{"type":"object","additionalProperties":False,"properties":{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["path","start_line","end_line"]},"strict":True},
    {"type":"function","name":"search_text","description":"Search literal text in repository files.","parameters":{"type":"object","additionalProperties":False,"properties":{"query":{"type":"string"},"prefix":{"type":"string"},"max_results":{"type":"integer"}},"required":["query","prefix","max_results"]},"strict":True},
    {"type":"function","name":"apply_patch","description":"Apply a controller-validated unified git diff.","parameters":{"type":"object","additionalProperties":False,"properties":{"patch":{"type":"string"}},"required":["patch"]},"strict":True},
    {"type":"function","name":"git_diff","description":"Inspect the current local candidate diff.","parameters":{"type":"object","additionalProperties":False,"properties":{"max_chars":{"type":"integer"}},"required":["max_chars"]},"strict":True},
    {"type":"function","name":"run_targeted_pytest","description":"Run pytest on one tests/ path only.","parameters":{"type":"object","additionalProperties":False,"properties":{"path":{"type":"string"}},"required":["path"]},"strict":True},
    {"type":"function","name":"run_quality_gate","description":"Run immutable QORE full gate: ruff, mypy, pytest coverage.","parameters":{"type":"object","additionalProperties":False,"properties":{},"required":[]},"strict":True},
    {"type":"function","name":"finish","description":"Finish READY after green full gate, or BLOCKED with a concrete reason.","parameters":{"type":"object","additionalProperties":False,"properties":{"status":{"type":"string","enum":["READY","BLOCKED"]},"summary":{"type":"string"},"notes":{"type":"array","items":{"type":"string"}}},"required":["status","summary","notes"]},"strict":True},
]


def api_call(key: str, body: dict[str, Any]) -> dict[str, Any]:
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


def validate_request(request: dict[str, Any], repo: Path) -> tuple[str, dict[str, Any]]:
    if request.get("schema_version") != "qore.codex.engineering.request.v1":
        raise WorkerError("unexpected Codex engineering request schema")
    source = request.get("source_main_sha")
    contract = request.get("engineering_contract")
    if not isinstance(source, str) or not SHA_RE.fullmatch(source):
        raise WorkerError("source_main_sha is invalid")
    if not isinstance(contract, dict) or contract.get("enabled") is not True:
        raise WorkerError("enabled engineering contract is required")
    if contract.get("target_repository") != "mezas3238-hue/qore-core":
        raise WorkerError("worker V2 is restricted to qore-core")
    if request.get("production_authority") is not False:
        raise WorkerError("Production authority is forbidden")
    if git(repo, "rev-parse", "HEAD").strip() != source:
        raise WorkerError("local checkout is not bound to source_main_sha")
    if git(repo, "status", "--porcelain").strip():
        raise WorkerError("local checkout must start clean")
    return source, contract


def dispatch_tool(tools: LocalTools, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "list_files": return tools.list_files(str(args["prefix"]), int(args["limit"]))
    if name == "read_file": return tools.read_file(str(args["path"]), int(args["start_line"]), int(args["end_line"]))
    if name == "search_text": return tools.search_text(str(args["query"]), str(args["prefix"]), int(args["max_results"]))
    if name == "apply_patch": return tools.apply_patch(str(args["patch"]))
    if name == "git_diff": return tools.git_diff(int(args["max_chars"]))
    if name == "run_targeted_pytest": return tools.run_targeted_pytest(str(args["path"]))
    if name == "run_quality_gate": return tools.run_quality_gate()
    raise WorkerError(f"unsupported tool: {name}")


def add_usage(total: dict[str, int], payload: dict[str, Any]) -> None:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
    output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    for key, value in (
        ("input_tokens", usage.get("input_tokens")),
        ("cached_tokens", input_details.get("cached_tokens")),
        ("cache_write_tokens", input_details.get("cache_write_tokens")),
        ("output_tokens", usage.get("output_tokens")),
        ("reasoning_tokens", output_details.get("reasoning_tokens")),
        ("total_tokens", usage.get("total_tokens")),
    ):
        if type(value) is int:
            total[key] = total.get(key, 0) + value


def make_result(
    repo: Path,
    source: str,
    contract_id: str,
    status: str,
    summary: str,
    notes: list[str],
    tools: LocalTools,
    turns: int,
) -> dict[str, Any]:
    return {
        "schema_version": "qore.codex.worker.result.v1",
        "source_main_sha": source,
        "contract_id": contract_id,
        "status": status,
        "summary": summary,
        "notes": notes,
        "changed_files": changed_files(repo),
        "quality_gate_success": tools.last_quality_success,
        "quality_gate_runs": tools.quality_runs,
        "diff_sha256": candidate_fingerprint(repo),
        "turns": turns,
        "production_authority": False,
    }


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
    source, contract = validate_request(request, repo)
    contract_id = str(contract.get("contract_id") or "")
    if not contract_id:
        raise WorkerError("contract_id is required")
    charter = Path(args.charter).read_text(encoding="utf-8")
    tools = LocalTools(repo)
    prompt = (
        "Execute this architect-issued engineering contract against the exact local checkout. "
        "This is bounded implementation, not PLAN-ONLY. Inspect before editing; use apply_patch for changes. "
        "You have no arbitrary shell, network, GitHub credential, merge/review authority, or Production authority. "
        "Never weaken tests or validation. Run run_quality_gate after the final patch before READY. "
        "If the contract cannot be safely completed in scope, finish BLOCKED.\n\nENGINEERING_REQUEST:\n"
        + json.dumps(request, separators=(",", ":"), ensure_ascii=False)
    )
    conversation: list[dict[str, Any]] = [{"role":"user","content":[{"type":"input_text","text":prompt}]}]
    usage_total: dict[str, int] = {}
    response_ids: list[str] = []
    final: dict[str, Any] | None = None
    completed_turns = 0

    for turn in range(1, MAX_TURNS + 1):
        if usage_total.get("total_tokens", 0) >= MAX_TOTAL_TOKENS:
            final = make_result(
                repo, source, contract_id, "BLOCKED",
                "Codex worker stopped at the hard cumulative API token budget.",
                [f"MAX_TOTAL_TOKENS={MAX_TOTAL_TOKENS}", "No candidate was published."],
                tools, completed_turns,
            )
            break
        payload = api_call(key, {
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
            "metadata": {"qore_role":"principal_engineer_worker_v2","qore_main_sha":source,"contract_id":contract_id[:64]},
        })
        completed_turns = turn
        if payload.get("status") != "completed":
            raise WorkerError(f"Codex response did not complete: {payload.get('status')}")
        if isinstance(payload.get("id"), str): response_ids.append(payload["id"])
        add_usage(usage_total, payload)
        outputs = payload.get("output")
        if not isinstance(outputs, list): raise WorkerError("Codex response output is invalid")
        calls = [item for item in outputs if isinstance(item, dict) and item.get("type") == "function_call"]
        if len(calls) != 1: raise WorkerError(f"expected exactly one function call, got {len(calls)}")
        call = calls[0]
        name, call_id, raw_args = call.get("name"), call.get("call_id"), call.get("arguments")
        if not all(isinstance(value, str) for value in (name, call_id, raw_args)):
            raise WorkerError("function call shape is invalid")
        try:
            parsed = json.loads(raw_args)
        except json.JSONDecodeError as exc:
            raise WorkerError("function arguments are invalid JSON") from exc
        if not isinstance(parsed, dict): raise WorkerError("function arguments must be an object")
        conversation.extend(item for item in outputs if isinstance(item, dict))

        if name == "finish":
            status, summary, notes = parsed.get("status"), parsed.get("summary"), parsed.get("notes")
            if status not in {"READY","BLOCKED"} or not isinstance(summary, str) or not isinstance(notes, list):
                raise WorkerError("finish arguments are invalid")
            if status == "READY":
                if not changed_files(repo): raise WorkerError("READY requires a non-empty candidate")
                if not tools.last_quality_success: raise WorkerError("READY requires green full gate after final patch")
            final = make_result(repo, source, contract_id, status, summary, [str(x) for x in notes], tools, turn)
            break

        try:
            result = dispatch_tool(tools, name, parsed)
            tool_output: dict[str, Any] = {"ok":True,"result":result}
        except (WorkerError, OSError, subprocess.TimeoutExpired) as exc:
            tool_output = {"ok":False,"error":str(exc)}
        conversation.append({"type":"function_call_output","call_id":call_id,"output":clip(json.dumps(tool_output, ensure_ascii=False))})

        if usage_total.get("total_tokens", 0) >= MAX_TOTAL_TOKENS:
            final = make_result(
                repo, source, contract_id, "BLOCKED",
                "Codex worker reached the hard cumulative API token budget after the latest bounded tool action.",
                [f"MAX_TOTAL_TOKENS={MAX_TOTAL_TOKENS}", "No candidate was published."],
                tools, turn,
            )
            break

    if final is None:
        final = make_result(
            repo, source, contract_id, "BLOCKED",
            "Codex worker reached the hard turn limit without a terminal engineering result.",
            [f"MAX_TURNS={MAX_TURNS}", "No candidate was published."], tools, completed_turns,
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    usage_total.update({"model":MODEL,"prompt_cache_key":PROMPT_CACHE_KEY,"response_ids":response_ids,"turns":final["turns"],"max_turns":MAX_TURNS,"max_total_tokens":MAX_TOTAL_TOKENS})
    Path(args.usage_output).write_text(json.dumps(usage_total, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"CODEX_ENGINEER_WORKER_V2_OK status={final['status']} main={source} contract={contract_id} changed_files={len(final['changed_files'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerError as exc:
        print(f"CODEX_ENGINEER_WORKER_V2_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(7) from exc
