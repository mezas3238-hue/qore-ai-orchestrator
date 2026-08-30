#!/usr/bin/env python3
"""Augment reviewer state with bounded live control-plane and technical evidence.

This collector never exposes provider API credentials. It uses only the GitHub
bridge token and keeps the result bounded so Sol can distinguish a genuinely
pending reviewer job from actionable reviewer-infrastructure work and can verify
which reviewer implementation is actually live on the default branch.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_REPO = "mezas3238-hue/qore-claude-reviewer"
DEEPSEEK_REPO = "mezas3238-hue/qore-deepseek-reviewer"
USER_AGENT = "qore-ai-orchestrator/1.0"
MAX_PRS = 8
MAX_ISSUES = 12
MAX_RUNS = 20
MAX_CLOSED_PRS = 4
MAX_CLOSED_ISSUES = 8
MAX_BODY_CHARS = 3500
MAX_TECHNICAL_FILE_CHARS = 40_000
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS = (
    ".github/workflows/deepseek-auto-dispatch.yml",
    ".github/workflows/deepseek-connection-test.yml",
    ".github/workflows/deepseek-qore-review.yml",
)
DEEPSEEK_PROJECTION_FILES = (
    "scripts/run_review_with_meter.py",
    "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
    "scripts/deepseek_reviewer_v2_1_entrypoint.py",
    "scripts/deepseek_reviewer_compact_budgeted_v20.py",
    "scripts/deepseek_reviewer_compact_budgeted_v19.py",
    "scripts/qg_package_contract.py",
    *DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS,
)


class ControlPlaneError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_json(
    repo: str,
    path: str,
    token: str,
    params: dict[str, str] | None = None,
) -> Any:
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}{query}",
        headers=_headers(token),
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise ControlPlaneError(
                f"{repo}{path}: HTTP {exc.code}; reviewer bridge requires "
                "Contents=Read-only, Pull requests=Read-only, Issues=Read-only, Actions=Read-only"
            ) from exc
        raise ControlPlaneError(f"{repo}{path}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ControlPlaneError(f"{repo}{path}: {type(exc).__name__}") from exc


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ControlPlaneError(f"{label}: expected lowercase 40-hex SHA")
    return value


def _decode_text_content(payload: Any, label: str) -> str:
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ControlPlaneError(f"{label}: contents payload is not a file")
    raw = payload.get("content")
    if not isinstance(raw, str):
        raise ControlPlaneError(f"{label}: contents payload lacks text content")
    try:
        encoded = "".join(raw.split())
        text = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ControlPlaneError(f"{label}: contents payload is not valid UTF-8/base64") from exc
    if len(text) > MAX_TECHNICAL_FILE_CHARS:
        raise ControlPlaneError(
            f"{label}: technical file exceeds bounded collector size "
            f"{len(text)} > {MAX_TECHNICAL_FILE_CHARS}"
        )
    return text


def _contents_file(repo: str, path: str, token: str, ref: str) -> tuple[dict[str, Any], str]:
    encoded = urllib.parse.quote(path, safe="/")
    payload = _api_json(repo, f"/contents/{encoded}", token, {"ref": ref})
    if not isinstance(payload, dict):
        raise ControlPlaneError(f"{repo}:{path}: contents response is invalid")
    blob_sha = _sha(payload.get("sha"), f"{repo}:{path}:blob")
    size = payload.get("size")
    if type(size) is not int or size < 0:
        raise ControlPlaneError(f"{repo}:{path}: size is invalid")
    text = _decode_text_content(payload, f"{repo}:{path}")
    return {"path": path, "blob_sha": blob_sha, "size": size}, text


def _main_identity(repo: str, token: str) -> dict[str, Any]:
    branch = _api_json(repo, "/branches/main", token)
    if not isinstance(branch, dict):
        raise ControlPlaneError(f"{repo}: main branch response is invalid")
    commit = branch.get("commit")
    if not isinstance(commit, dict):
        raise ControlPlaneError(f"{repo}: main branch commit is invalid")
    sha = _sha(commit.get("sha"), f"{repo}:main")
    inner = commit.get("commit")
    if not isinstance(inner, dict):
        raise ControlPlaneError(f"{repo}: main commit metadata is invalid")
    tree = inner.get("tree")
    if not isinstance(tree, dict):
        raise ControlPlaneError(f"{repo}: main tree metadata is invalid")
    tree_sha = _sha(tree.get("sha"), f"{repo}:main tree")
    verification = inner.get("verification")
    if not isinstance(verification, dict):
        verification = {}
    parents_raw = commit.get("parents")
    parents: list[str] = []
    if isinstance(parents_raw, list):
        for item in parents_raw:
            if isinstance(item, dict):
                parents.append(_sha(item.get("sha"), f"{repo}:main parent"))
    return {
        "sha": sha,
        "tree_sha": tree_sha,
        "commit_message": inner.get("message"),
        "parents": parents,
        "signature_verified": verification.get("verified") is True,
        "signature_reason": verification.get("reason"),
    }


def _labels(value: Any) -> list[str]:
    result: list[str] = []
    if not isinstance(value, list):
        return result
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            result.append(item["name"])
        elif isinstance(item, str):
            result.append(item)
    return result


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "display_title": run.get("display_title"),
        "event": run.get("event"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "run_attempt": run.get("run_attempt"),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
    }


def _latest_run_for_head(runs: list[dict[str, Any]], head_sha: Any) -> dict[str, Any] | None:
    if not isinstance(head_sha, str):
        return None
    for run in runs:
        if run.get("head_sha") == head_sha:
            return _run_summary(run)
    return None


def _pull_summary(item: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any]:
    head = item.get("head") if isinstance(item.get("head"), dict) else {}
    base = item.get("base") if isinstance(item.get("base"), dict) else {}
    head_sha = head.get("sha")
    merged_at = item.get("merged_at")
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "draft": item.get("draft"),
        "merged": isinstance(merged_at, str) and bool(merged_at),
        "merged_at": merged_at,
        "merge_commit_sha": item.get("merge_commit_sha"),
        "updated_at": item.get("updated_at"),
        "base_ref": base.get("ref"),
        "base_sha": base.get("sha"),
        "head_ref": head.get("ref"),
        "head_sha": head_sha,
        "changed_files": item.get("changed_files"),
        "additions": item.get("additions"),
        "deletions": item.get("deletions"),
        "body": str(item.get("body") or "")[:MAX_BODY_CHARS],
        "latest_head_run": _latest_run_for_head(runs, head_sha),
    }


def _issue_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "state_reason": item.get("state_reason"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "closed_at": item.get("closed_at"),
        "labels": _labels(item.get("labels")),
        "body": str(item.get("body") or "")[:MAX_BODY_CHARS],
    }


def _require_markers(text: str, label: str, markers: tuple[str, ...]) -> None:
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise ControlPlaneError(f"{label}: required live-contract markers missing: {missing!r}")


def _deepseek_projection(token: str, main_sha: str) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    texts: dict[str, str] = {}
    for path in DEEPSEEK_PROJECTION_FILES:
        evidence, text = _contents_file(DEEPSEEK_REPO, path, token, main_sha)
        files[path] = evidence
        texts[path] = text

    meter = texts["scripts/run_review_with_meter.py"]
    stable = texts["scripts/deepseek_reviewer_v2_1_1_entrypoint.py"]
    v21 = texts["scripts/deepseek_reviewer_v2_1_entrypoint.py"]
    compact_v20 = texts["scripts/deepseek_reviewer_compact_budgeted_v20.py"]
    compact_v19 = texts["scripts/deepseek_reviewer_compact_budgeted_v19.py"]
    review_workflow = texts[".github/workflows/deepseek-qore-review.yml"]
    auto_dispatch = texts[".github/workflows/deepseek-auto-dispatch.yml"]

    _require_markers(
        meter,
        "DeepSeek meter",
        (
            '"deepseek_reviewer_v2_1_1_entrypoint.py"',
            '"deepseek_reviewer_compact_budgeted_v20.py"',
            'os.environ.get("DEEPSEEK_REVIEWER_PROFILE", "compact-budgeted")',
            'elif _REVIEWER_PROFILE == "compact-budgeted":',
            'elif _REVIEWER_PROFILE == "stable":',
        ),
    )
    _require_markers(
        review_workflow,
        "DeepSeek review workflow",
        (
            "run-name: DeepSeek QORE review · ${{ inputs.package_id }}",
            "DEEPSEEK_MODEL: deepseek-v4-pro",
            'test "$LIVE_BASE" = "$EXPECTED_BASE"',
            'test "$LIVE_HEAD" = "$EXPECTED_HEAD"',
            'test "$LIVE_SYNTHETIC" = "$EXPECTED_SYNTHETIC"',
            'test "$PARENTS" = "$EXPECTED_BASE $EXPECTED_HEAD"',
            'test "$SYNTHETIC_TREE" = "$HEAD_TREE"',
            "Revalidate complete frozen PR immediately before publication",
        ),
    )
    _require_markers(
        auto_dispatch,
        "DeepSeek auto-dispatch",
        (
            "requests/current.json",
            "benchmarks/current.json",
            "Refusing ambiguous push: review and benchmark requests changed together.",
            "scripts/qg_package_contract.py",
            "gh workflow run deepseek-qore-review.yml",
        ),
    )
    _require_markers(
        stable,
        "DeepSeek stable entrypoint",
        (
            "deepseek_reviewer_v2_0_entrypoint",
            "deepseek_reviewer_v2_1_entrypoint",
            "DEEPSEEK_MAX_MANDATORY_CHANGED_CHARS",
            "DEEPSEEK_MAX_FINAL_EVIDENCE_CHARS",
        ),
    )
    _require_markers(
        v21,
        "DeepSeek V2.1 reasoning contract",
        (
            '"DEEPSEEK_TOTAL_COMPLETION_TOKEN_BUDGET", "100000"',
            '"DEEPSEEK_VERDICT_RESERVE_TOKENS", "12000"',
            'thinking=True',
            'thinking=False',
            'v2_1_same_model_extractor=True',
            'v2_1_flash_substitution=False',
            'v2_1_cot_continuation=False',
            'if stage == "final-fallback":',
        ),
    )
    _require_markers(
        compact_v19,
        "DeepSeek compact QG correction",
        (
            "_QG_EVIDENCE_MAX_CHARS = 8000",
            "Full command windows were parsed and validated internally",
            "authenticated_command_summaries",
            "compact QORE CI evidence exceeds its hard transport bound",
        ),
    )
    _require_markers(
        compact_v20,
        "DeepSeek compact operational entrypoint",
        (
            "deepseek_reviewer_compact_budgeted_v19",
            "_scanner_r62g_exact",
        ),
    )

    authorized = [files[path] for path in DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS]
    if len(authorized) != 3:
        raise ControlPlaneError("DeepSeek stable profile must expose exactly three review-lane workflows")

    return {
        "bound_main_sha": main_sha,
        "authoritative_model": "deepseek-v4-pro",
        "operational_default": {
            "profile": "compact-budgeted",
            "entrypoint": "scripts/deepseek_reviewer_compact_budgeted_v20.py",
            "selection_source": "scripts/run_review_with_meter.py",
        },
        "stable_fallback": {
            "profile": "stable",
            "entrypoint": "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
            "completion_budget_default": 100000,
            "verdict_reserve_default": 12000,
            "authoritative_analysis_thinking": True,
            "same_model_non_thinking_extractor": True,
            "flash_substitution": False,
            "cot_continuation": False,
            "legacy_full_evidence_fallback": False,
        },
        "stable_profile_authorized_workflows": {
            "count": 3,
            "files": authorized,
        },
        "binding_contract": {
            "run_name_package_bound": True,
            "live_pr_base_head_synthetic_revalidated": True,
            "synthetic_parents_and_tree_revalidated": True,
            "pre_publication_freeze_revalidated": True,
            "request_and_benchmark_same_push_refused": True,
            "request_contract_validated_before_dispatch": True,
            "review_request_source": "requests/current.json",
            "benchmark_request_source": "benchmarks/current.json",
        },
        "qg_transport_contract": {
            "raw_qg_parsed_and_validated_internally": True,
            "transported_qg_evidence_max_chars": 8000,
            "transport_only_authenticated_summary_and_checkout_proof": True,
        },
        "files": [files[path] for path in DEEPSEEK_PROJECTION_FILES],
    }


def collect_repo_control_plane(repo: str, token: str) -> dict[str, Any]:
    main_identity = _main_identity(repo, token)
    pulls_payload = _api_json(
        repo,
        "/pulls",
        token,
        {"state": "open", "sort": "updated", "direction": "desc", "per_page": str(MAX_PRS)},
    )
    issues_payload = _api_json(
        repo,
        "/issues",
        token,
        {"state": "open", "sort": "updated", "direction": "desc", "per_page": str(MAX_ISSUES)},
    )
    closed_pulls_payload = _api_json(
        repo,
        "/pulls",
        token,
        {"state": "closed", "sort": "updated", "direction": "desc", "per_page": str(MAX_CLOSED_PRS)},
    )
    closed_issues_payload = _api_json(
        repo,
        "/issues",
        token,
        {"state": "closed", "sort": "updated", "direction": "desc", "per_page": str(MAX_CLOSED_ISSUES)},
    )
    runs_payload = _api_json(repo, "/actions/runs", token, {"per_page": str(MAX_RUNS)})
    if not isinstance(pulls_payload, list):
        raise ControlPlaneError(f"{repo}: pull list is invalid")
    if not isinstance(issues_payload, list):
        raise ControlPlaneError(f"{repo}: issue list is invalid")
    if not isinstance(closed_pulls_payload, list):
        raise ControlPlaneError(f"{repo}: closed pull list is invalid")
    if not isinstance(closed_issues_payload, list):
        raise ControlPlaneError(f"{repo}: closed issue list is invalid")
    if not isinstance(runs_payload, dict) or not isinstance(runs_payload.get("workflow_runs"), list):
        raise ControlPlaneError(f"{repo}: workflow run list is invalid")

    runs = [item for item in runs_payload["workflow_runs"] if isinstance(item, dict)]
    pulls = [
        _pull_summary(item, runs)
        for item in pulls_payload[:MAX_PRS]
        if isinstance(item, dict)
    ]

    closed_pulls: list[dict[str, Any]] = []
    for item in closed_pulls_payload[:MAX_CLOSED_PRS]:
        if not isinstance(item, dict) or type(item.get("number")) is not int:
            continue
        detail = _api_json(repo, f"/pulls/{item['number']}", token)
        if not isinstance(detail, dict):
            raise ControlPlaneError(f"{repo}: closed pull detail is invalid")
        closed_pulls.append(_pull_summary(detail, runs))

    issues = [
        _issue_summary(item)
        for item in issues_payload[:MAX_ISSUES]
        if isinstance(item, dict) and not isinstance(item.get("pull_request"), dict)
    ]
    closed_issues = [
        _issue_summary(item)
        for item in closed_issues_payload[:MAX_CLOSED_ISSUES]
        if isinstance(item, dict) and not isinstance(item.get("pull_request"), dict)
    ]

    result: dict[str, Any] = {
        "repository": repo,
        "visibility": "AVAILABLE",
        "main": main_identity,
        "open_pull_requests": pulls,
        "recent_closed_pull_requests": closed_pulls,
        "open_issues": issues,
        "recent_closed_issues": closed_issues,
        "recent_action_runs": [_run_summary(run) for run in runs[:MAX_RUNS]],
    }
    if repo == DEEPSEEK_REPO:
        result["technical_projection"] = _deepseek_projection(token, main_identity["sha"])
    return result


def augment_state(state: dict[str, Any], token: str) -> dict[str, Any]:
    if not token:
        state.setdefault("errors", []).append(
            "reviewer-control-plane: QORE_REVIEWER_DISPATCH_TOKEN is missing"
        )
        return state

    for key, repo in (("claude", CLAUDE_REPO), ("deepseek", DEEPSEEK_REPO)):
        reviewer = state.get(key)
        if not isinstance(reviewer, dict):
            state.setdefault("errors", []).append(
                f"reviewer-control-plane:{key}: base reviewer state is unavailable"
            )
            continue
        try:
            reviewer["control_plane"] = collect_repo_control_plane(repo, token)
        except ControlPlaneError as exc:
            reviewer["control_plane"] = {
                "repository": repo,
                "visibility": "UNAVAILABLE",
                "main": None,
                "open_pull_requests": [],
                "recent_closed_pull_requests": [],
                "open_issues": [],
                "recent_closed_issues": [],
                "recent_action_runs": [],
            }
            state.setdefault("errors", []).append(f"reviewer-control-plane:{key}:{exc}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    source = Path(args.state)
    state = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit("external reviewer state must be an object")

    token = os.environ.get("QORE_REVIEWER_DISPATCH_TOKEN", "").strip()
    augmented = augment_state(state, token)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(augmented, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    errors = augmented.get("errors") if isinstance(augmented.get("errors"), list) else []
    print(
        "REVIEWER_CONTROL_PLANE errors={} claude={} deepseek={}".format(
            len(errors),
            ((augmented.get("claude") or {}).get("control_plane") or {}).get("visibility"),
            ((augmented.get("deepseek") or {}).get("control_plane") or {}).get("visibility"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
