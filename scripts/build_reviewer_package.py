#!/usr/bin/env python3
"""Resolve an exact qore-core PR freeze/QG and build a reviewer request package."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

QORE_REPO = "mezas3238-hue/qore-core"
API = f"https://api.github.com/repos/{QORE_REPO}"
USER_AGENT = "qore-ai-orchestrator/1.0"
QORE_CI_WORKFLOW_ID = 328173079
QORE_CI_WORKFLOW_NAME = "QORE CI"
QORE_CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TIMESTAMP_RE = re.compile(r"^\ufeff?\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ")
DEEPSEEK_MARKER_RE = re.compile(r"<!-- QORE-DEEPSEEK-REVIEW package=(?P<package>[^ ]+) head=(?P<head>[0-9a-f]{40}) -->")


class PackageError(RuntimeError):
    pass


@dataclass(frozen=True)
class QualitySummary:
    run_id: int
    job_id: int
    ruff_passed: bool
    mypy_source_files: int
    pytest_collected: int
    pytest_passed: int
    pytest_warnings: int
    coverage_total_statements: int
    coverage_missed_statements: int
    coverage_percent: int


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("QORE_CORE_READ_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_json(path: str, params: dict[str, str] | None = None) -> Any:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(API + path + query, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PackageError(f"GitHub API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PackageError(f"GitHub API {path} failed: {type(exc).__name__}") from exc


def api_text(path: str) -> str:
    request = urllib.request.Request(API + path, headers=_headers())
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8-sig", errors="strict")
    except urllib.error.HTTPError as exc:
        raise PackageError(f"GitHub log API {path} failed with HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, UnicodeError) as exc:
        raise PackageError(f"GitHub log API {path} failed: {type(exc).__name__}") from exc


def normalized_lines(log_text: str) -> list[str]:
    lines: list[str] = []
    for raw in log_text.splitlines():
        line = TIMESTAMP_RE.sub("", raw)
        lines.append(ANSI_RE.sub("", line).rstrip())
    return lines


def run_step_window(lines: list[str], command: str) -> list[str]:
    marker = f"##[group]Run {command}"
    starts = [index for index, line in enumerate(lines) if line == marker]
    if len(starts) != 1:
        raise PackageError(f"QG log must contain exactly one {command!r} step; found {len(starts)}")
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("##[group]Run "):
            end = index
            break
    return lines[start:end]


def single_match(lines: list[str], pattern: re.Pattern[str], label: str) -> re.Match[str]:
    matches = [match for line in lines if (match := pattern.fullmatch(line))]
    if len(matches) != 1:
        raise PackageError(f"QG log must contain exactly one {label}; found {len(matches)}")
    return matches[0]


def parse_quality_log(log_text: str, expected_synthetic: str, run_id: int, job_id: int) -> QualitySummary:
    lines = normalized_lines(log_text)
    checkout = run_step_window(lines, "actions/checkout@v4")
    command = "[command]/usr/bin/git log -1 --format=%H"
    indexes = [index for index, line in enumerate(checkout) if line == command]
    if len(indexes) != 1:
        raise PackageError("QG checkout evidence lacks unique git log command")
    following = [line for line in checkout[indexes[0] + 1 :] if line]
    if not following or following[0] != expected_synthetic:
        raise PackageError("QG checkout did not execute the expected synthetic commit")

    ruff = run_step_window(lines, "ruff check .")
    if sum(line == "All checks passed!" for line in ruff) != 1:
        raise PackageError("Ruff QG step lacks unique clean marker")

    mypy = run_step_window(lines, "mypy src tests")
    mypy_match = single_match(
        mypy,
        re.compile(r"Success: no issues found in (\d+) source files"),
        "Mypy success summary",
    )

    pytest = run_step_window(lines, "pytest --cov=src/qore --cov-report=term-missing")
    collected = single_match(pytest, re.compile(r"collected (\d+) items"), "pytest collection summary")
    coverage = single_match(
        pytest,
        re.compile(r"TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%"),
        "TOTAL coverage summary",
    )
    passed = single_match(
        pytest,
        re.compile(r"=+\s+(\d+) passed(?:, (\d+) warnings?)? in \d+(?:\.\d+)?s(?: \([^)]*\))?\s+=+"),
        "pytest pass/warnings summary",
    )

    summary = QualitySummary(
        run_id=run_id,
        job_id=job_id,
        ruff_passed=True,
        mypy_source_files=int(mypy_match.group(1)),
        pytest_collected=int(collected.group(1)),
        pytest_passed=int(passed.group(1)),
        pytest_warnings=int(passed.group(2) or 0),
        coverage_total_statements=int(coverage.group(1)),
        coverage_missed_statements=int(coverage.group(2)),
        coverage_percent=int(coverage.group(3)),
    )
    if summary.pytest_collected != summary.pytest_passed:
        raise PackageError("QG pytest gate is not all-pass")
    if summary.coverage_missed_statements > summary.coverage_total_statements:
        raise PackageError("QG coverage misses exceed total statements")
    if summary.coverage_percent > 100:
        raise PackageError("QG coverage percent is invalid")
    return summary


def resolve_freeze(pr_number: int) -> tuple[str, str, str]:
    pr = api_json(f"/pulls/{pr_number}")
    if not isinstance(pr, dict):
        raise PackageError("PR payload is not an object")
    if pr.get("state") != "open" or pr.get("merged") is True:
        raise PackageError("review target PR is not open and unmerged")
    base = (pr.get("base") or {}).get("sha")
    head = (pr.get("head") or {}).get("sha")
    synthetic = pr.get("merge_commit_sha")
    for value, label in ((base, "BASE"), (head, "HEAD"), (synthetic, "SYNTHETIC")):
        if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
            raise PackageError(f"{label} is not a lowercase 40-hex SHA")

    synthetic_payload = api_json(f"/git/commits/{synthetic}")
    head_payload = api_json(f"/git/commits/{head}")
    if not isinstance(synthetic_payload, dict) or not isinstance(head_payload, dict):
        raise PackageError("commit payload is incomplete")
    parents = [p.get("sha") for p in synthetic_payload.get("parents", []) if isinstance(p, dict)]
    if parents != [base, head]:
        raise PackageError("synthetic parents do not equal BASE HEAD")
    synthetic_tree = (synthetic_payload.get("tree") or {}).get("sha")
    head_tree = (head_payload.get("tree") or {}).get("sha")
    if synthetic_tree != head_tree:
        raise PackageError("synthetic tree does not equal HEAD tree")
    return base, head, synthetic


def resolve_quality(head: str, synthetic: str) -> QualitySummary:
    runs_payload = api_json(
        "/actions/runs",
        {"event": "pull_request", "status": "success", "head_sha": head, "per_page": "100"},
    )
    if not isinstance(runs_payload, dict):
        raise PackageError("workflow-run payload is not an object")
    candidates = []
    for run in runs_payload.get("workflow_runs", []):
        if not isinstance(run, dict):
            continue
        if (
            run.get("workflow_id") == QORE_CI_WORKFLOW_ID
            and run.get("name") == QORE_CI_WORKFLOW_NAME
            and run.get("path") == QORE_CI_WORKFLOW_PATH
            and run.get("event") == "pull_request"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("head_sha") == head
        ):
            candidates.append(run)
    if not candidates:
        raise PackageError("no successful exact-head QORE CI pull_request run found")
    candidates.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)
    run = candidates[0]
    run_id = run.get("id")
    if type(run_id) is not int or run_id <= 0:
        raise PackageError("QG run ID is invalid")

    jobs_payload = api_json(f"/actions/runs/{run_id}/jobs", {"per_page": "100"})
    if not isinstance(jobs_payload, dict):
        raise PackageError("QG jobs payload is not an object")
    jobs = [
        job
        for job in jobs_payload.get("jobs", [])
        if isinstance(job, dict)
        and job.get("name") == "quality"
        and job.get("status") == "completed"
        and job.get("conclusion") == "success"
        and job.get("head_sha") == head
        and job.get("run_id") == run_id
    ]
    if len(jobs) != 1:
        raise PackageError(f"expected exactly one successful quality job; found {len(jobs)}")
    job_id = jobs[0].get("id")
    if type(job_id) is not int or job_id <= 0:
        raise PackageError("QG job ID is invalid")
    log_text = api_text(f"/actions/jobs/{job_id}/logs")
    return parse_quality_log(log_text, synthetic, run_id, job_id)


def has_exact_deepseek_expert(snapshot: dict[str, Any], pr_number: int, head: str) -> bool:
    for pr in snapshot.get("open_pull_requests", []):
        if not isinstance(pr, dict) or pr.get("number") != pr_number:
            continue
        for review in pr.get("reviews", []):
            if not isinstance(review, dict) or review.get("commit_id") != head:
                continue
            body = str(review.get("body") or "")
            for match in DEEPSEEK_MARKER_RE.finditer(body):
                package = match.group("package")
                if match.group("head") == head and "DS-EXPERT" in package:
                    return True
        for comment in pr.get("conversation_comments", []):
            if not isinstance(comment, dict):
                continue
            body = str(comment.get("body") or "")
            for match in DEEPSEEK_MARKER_RE.finditer(body):
                package = match.group("package")
                if match.group("head") == head and "DS-EXPERT" in package:
                    return True
    return False


def build_prompt(
    *,
    decision: dict[str, Any],
    contract: dict[str, Any],
    base: str,
    head: str,
    synthetic: str,
    qg: QualitySummary,
    package_id: str,
) -> str:
    qg_payload = asdict(qg)
    lines = [
        f"# QORE orchestrator package — {package_id}",
        "",
        "This package was issued by GPT-5.6 Sol acting as QORE Principal Architect.",
        "GitHub/qore-core remains the sole source of truth. Review only the exact frozen candidate below.",
        "",
        "## Exact freeze",
        f"- PR: #{contract['pr_number']}",
        f"- BASE: `{base}`",
        f"- HEAD: `{head}`",
        f"- SYNTHETIC: `{synthetic}`",
        f"- architect source main: `{decision['source_main_sha']}`",
        "",
        "## Authoritative Quality Gate",
        f"- run: `{qg.run_id}`",
        f"- job: `{qg.job_id}`",
        "- Ruff: PASS",
        f"- Mypy: {qg.mypy_source_files} source files",
        f"- Pytest: {qg.pytest_collected} collected / {qg.pytest_passed} passed / {qg.pytest_warnings} warnings",
        f"- Coverage: {qg.coverage_total_statements} statements / {qg.coverage_missed_statements} missed / {qg.coverage_percent}%",
        "",
        "<!-- QORE-EXACT-QG " + json.dumps(qg_payload, sort_keys=True, separators=(",", ":")) + " -->",
        "",
        "## Review contract",
        f"- kind: `{contract['review_kind']}`",
        f"- contract_id: `{contract['contract_id']}`",
        f"- objective: {contract['objective']}",
        "",
        "### Scope",
        *[f"- {item}" for item in contract.get("scope", [])],
        "",
        "### Adversarial foci",
        *[f"- {item}" for item in contract.get("adversarial_foci", [])],
        "",
        "### Acceptance",
        *[f"- {item}" for item in contract.get("acceptance", [])],
        "",
        "### Forbidden",
        *[f"- {item}" for item in contract.get("forbidden", [])],
        "",
        "## Required behavior",
        "- Be independent and adversarial. Do not assume Sol or another agent is correct.",
        "- Report only reproducible material findings tied to the exact frozen candidate.",
        "- Distinguish a true defect from missing evidence and from a false positive.",
        "- Do not modify qore-core during this review.",
        "- Do not authorize Production, productive credentials, real capital, deposits/withdrawals, or real-money execution.",
        "- If the candidate changes, this review becomes obsolete.",
        "",
        "## Architect decision context",
        "```json",
        json.dumps(
            {
                "status": decision.get("status"),
                "decision": decision.get("decision"),
                "roadmap_anchor": decision.get("roadmap_anchor"),
                "evidence": decision.get("evidence"),
                "risk_gates": decision.get("risk_gates"),
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--orchestrator-run-id", required=True)
    parser.add_argument("--prompt-output", required=True)
    parser.add_argument("--request-output", required=True)
    parser.add_argument("--metadata-output", required=True)
    args = parser.parse_args()

    decision = json.loads(Path(args.decision).read_text(encoding="utf-8"))
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    if decision.get("source_main_sha") != snapshot.get("main_sha"):
        raise SystemExit("Architect decision and snapshot main SHA do not match")

    contract = decision.get("review_contract")
    actor = decision.get("next_actor")
    if actor not in {"CLAUDE_CODE", "DEEPSEEK"} or not isinstance(contract, dict) or contract.get("enabled") is not True:
        raise SystemExit("Architect decision does not authorize an external reviewer package")
    pr_number = contract.get("pr_number")
    if type(pr_number) is not int or pr_number <= 0:
        raise SystemExit("Review contract PR number is invalid")

    run_id_raw = str(args.orchestrator_run_id)
    if not run_id_raw.isdigit() or int(run_id_raw) <= 0:
        raise SystemExit("Orchestrator run ID is invalid")

    try:
        base, head, synthetic = resolve_freeze(pr_number)
        qg = resolve_quality(head, synthetic)
    except PackageError as exc:
        raise SystemExit(f"REVIEW_PACKAGE_BLOCKED: {exc}") from exc

    main_short = str(decision.get("source_main_sha") or "")[:12]
    kind = contract.get("review_kind")
    if actor == "CLAUDE_CODE":
        if kind != "CLAUDE_TECHNICAL":
            raise SystemExit("Claude routing kind mismatch")
        package_id = f"QORE-SOL-{main_short}-CLAUDE-R{run_id_raw}"
        target_repo = "mezas3238-hue/qore-claude-reviewer"
    else:
        if kind == "DEEPSEEK_EXPERT":
            suffix = "EXPERT"
            review_mode = "expert"
        elif kind == "DEEPSEEK_CODER":
            if not has_exact_deepseek_expert(snapshot, pr_number, head):
                raise SystemExit("REVIEW_PACKAGE_BLOCKED: DeepSeek Coder requires exact-head Expert evidence first")
            suffix = "CODER"
            review_mode = "coder"
        else:
            raise SystemExit("DeepSeek routing kind mismatch")
        package_id = f"QORE-SOL-{main_short}-DS-{suffix}-R{run_id_raw}"
        target_repo = "mezas3238-hue/qore-deepseek-reviewer"

    prompt_path = f"prompts/orchestrator/{package_id.lower()}.md"
    prompt = build_prompt(
        decision=decision,
        contract=contract,
        base=base,
        head=head,
        synthetic=synthetic,
        qg=qg,
        package_id=package_id,
    )

    if actor == "CLAUDE_CODE":
        qg_dict = asdict(qg)
        request_payload = {
            "expected_base": base,
            "expected_head": head,
            "expected_synthetic": synthetic,
            "package_id": package_id,
            "pr_number": pr_number,
            "prompt_path": prompt_path,
            "qg": {
                "expected": {
                    key: value
                    for key, value in qg_dict.items()
                    if key not in {"run_id", "job_id"}
                },
                "job_id": qg.job_id,
                "run_id": qg.run_id,
            },
        }
    else:
        request_payload = {
            "pr_number": pr_number,
            "package_id": package_id,
            "expected_base": base,
            "expected_head": head,
            "expected_synthetic": synthetic,
            "qg_summary": asdict(qg),
            "review_mode": review_mode,
            "prompt_path": prompt_path,
            "dispatch_nonce": f"SOL-R{run_id_raw}-{head}",
        }

    prompt_output = Path(args.prompt_output)
    request_output = Path(args.request_output)
    metadata_output = Path(args.metadata_output)
    for path in (prompt_output, request_output, metadata_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    prompt_output.write_text(prompt + "\n", encoding="utf-8")
    request_output.write_text(json.dumps(request_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metadata_output.write_text(
        json.dumps(
            {
                "actor": actor,
                "target_repo": target_repo,
                "package_id": package_id,
                "prompt_path": prompt_path,
                "pr_number": pr_number,
                "base": base,
                "head": head,
                "synthetic": synthetic,
                "qg": asdict(qg),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"REVIEW_PACKAGE_OK actor={actor} package={package_id} pr={pr_number} head={head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
