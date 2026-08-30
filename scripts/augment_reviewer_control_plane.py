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
STABLE_MANIFEST_RE = re.compile(r"^QORE-DEEPSEEK-.*-STABLE\.json$")
DEEPSEEK_STABLE_MANIFEST_DIR = "profiles"

DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS = (
    ".github/workflows/deepseek-auto-dispatch.yml",
    ".github/workflows/deepseek-connection-test.yml",
    ".github/workflows/deepseek-qore-review.yml",
)
DEEPSEEK_PROJECTION_CONTRACT_FILES = (
    "scripts/run_review_with_meter.py",
    "scripts/deepseek_reviewer_v2_1_1_entrypoint.py",
    "scripts/deepseek_reviewer_v2_1_entrypoint.py",
    "scripts/exact_qg_evidence.py",
    ".github/workflows/deepseek-auto-dispatch.yml",
    ".github/workflows/deepseek-qore-review.yml",
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


def _contents_payload(repo: str, path: str, token: str, ref: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(path, safe="/")
    payload = _api_json(repo, f"/contents/{encoded}", token, {"ref": ref})
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise ControlPlaneError(f"{repo}:{path}: contents response is not a file")
    return payload


def _contents_metadata(repo: str, path: str, token: str, ref: str) -> dict[str, Any]:
    payload = _contents_payload(repo, path, token, ref)
    blob_sha = _sha(payload.get("sha"), f"{repo}:{path}:blob")
    size = payload.get("size")
    if type(size) is not int or size < 0:
        raise ControlPlaneError(f"{repo}:{path}: size is invalid")
    return {"path": path, "blob_sha": blob_sha, "size": size}


def _contents_file(repo: str, path: str, token: str, ref: str) -> tuple[dict[str, Any], str]:
    payload = _contents_payload(repo, path, token, ref)
    metadata = {
        "path": path,
        "blob_sha": _sha(payload.get("sha"), f"{repo}:{path}:blob"),
        "size": payload.get("size"),
    }
    if type(metadata["size"]) is not int or metadata["size"] < 0:
        raise ControlPlaneError(f"{repo}:{path}: size is invalid")
    return metadata, _decode_text_content(payload, f"{repo}:{path}")


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


def _manifest_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ControlPlaneError(f"{label}: expected non-empty path-to-blob mapping")
    result: dict[str, str] = {}
    for path, blob in value.items():
        if not isinstance(path, str) or not path:
            raise ControlPlaneError(f"{label}: path is invalid")
        result[path] = _sha(blob, f"{label}:{path}")
    return result


def _deepseek_stable_manifest(
    token: str, main_sha: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    listing = _api_json(DEEPSEEK_REPO, f"/contents/{DEEPSEEK_STABLE_MANIFEST_DIR}", token, {"ref": main_sha})
    if not isinstance(listing, list):
        raise ControlPlaneError("DeepSeek profiles directory listing is invalid")
    stable_entries = [
        item
        for item in listing
        if isinstance(item, dict)
        and item.get("type") == "file"
        and isinstance(item.get("name"), str)
        and STABLE_MANIFEST_RE.fullmatch(item["name"]) is not None
    ]
    if len(stable_entries) != 1:
        raise ControlPlaneError(
            f"DeepSeek must expose exactly one STABLE profile manifest; found {len(stable_entries)}"
        )
    entry = stable_entries[0]
    path = str(entry.get("path") or "")
    if not path:
        raise ControlPlaneError("DeepSeek STABLE manifest path is missing")
    metadata, text = _contents_file(DEEPSEEK_REPO, path, token, main_sha)
    listed_sha = _sha(entry.get("sha"), "DeepSeek STABLE manifest listing blob")
    if metadata["blob_sha"] != listed_sha:
        raise ControlPlaneError("DeepSeek STABLE manifest listing/content blob mismatch")
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ControlPlaneError("DeepSeek STABLE manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise ControlPlaneError("DeepSeek STABLE manifest must be an object")
    return manifest, metadata, path


def _deepseek_projection(token: str, main_sha: str) -> dict[str, Any]:
    manifest, manifest_file, manifest_path = _deepseek_stable_manifest(token, main_sha)
    if manifest.get("profile_id") != "QORE-DEEPSEEK-V2.1.1-STABLE":
        raise ControlPlaneError("DeepSeek sole STABLE manifest has unexpected profile_id")
    if manifest.get("status") != "stable" or manifest.get("model") != "deepseek-v4-pro":
        raise ControlPlaneError("DeepSeek STABLE manifest status/model contract changed")

    entrypoint = manifest.get("entrypoint")
    entrypoint_blob = _sha(manifest.get("entrypoint_blob"), "DeepSeek stable entrypoint blob")
    if entrypoint != "scripts/deepseek_reviewer_v2_1_1_entrypoint.py":
        raise ControlPlaneError("DeepSeek STABLE entrypoint changed without collector recertification")

    meter_contract = manifest.get("meter")
    if not isinstance(meter_contract, dict):
        raise ControlPlaneError("DeepSeek STABLE meter contract is missing")
    meter_path = meter_contract.get("path")
    if meter_path != "scripts/run_review_with_meter.py":
        raise ControlPlaneError("DeepSeek STABLE meter path changed")
    meter_blob = _sha(meter_contract.get("blob"), "DeepSeek STABLE meter blob")
    if meter_contract.get("ordinary_route") != entrypoint or meter_contract.get("default_profile") != "stable":
        raise ControlPlaneError("DeepSeek STABLE manifest does not govern the ordinary meter route")

    exact_qg = manifest.get("exact_qg_contract")
    if not isinstance(exact_qg, dict) or exact_qg.get("required") is not True:
        raise ControlPlaneError("DeepSeek STABLE exact-QG contract is missing")
    exact_qg_path = exact_qg.get("helper")
    qg_contract_path = exact_qg.get("package_contract")
    if exact_qg_path != "scripts/exact_qg_evidence.py" or qg_contract_path != "scripts/qg_package_contract.py":
        raise ControlPlaneError("DeepSeek STABLE exact-QG helper contract changed")
    exact_qg_blob = _sha(exact_qg.get("helper_blob"), "DeepSeek exact-QG helper blob")
    qg_contract_blob = _sha(exact_qg.get("package_contract_blob"), "DeepSeek QG package contract blob")
    if exact_qg.get("max_chars") != 8000:
        raise ControlPlaneError("DeepSeek exact-QG transport bound changed")

    alternate_profiles = manifest.get("alternate_profiles")
    if not isinstance(alternate_profiles, dict):
        raise ControlPlaneError("DeepSeek alternate profile contract is missing")
    compact = alternate_profiles.get("compact-budgeted")
    benchmark = alternate_profiles.get("benchmark-compact")
    if not isinstance(compact, dict) or not isinstance(benchmark, dict):
        raise ControlPlaneError("DeepSeek compact/benchmark alternate profile contract is missing")
    if compact.get("ordinary_default") is not False or benchmark.get("ordinary_default") is not False:
        raise ControlPlaneError("DeepSeek alternate profile cannot be an ordinary default")
    compact_path = compact.get("entrypoint")
    benchmark_path = benchmark.get("entrypoint")
    if compact_path != "scripts/deepseek_reviewer_compact_budgeted_v20.py":
        raise ControlPlaneError("DeepSeek compact alternate entrypoint changed")
    compact_blob = _sha(compact.get("blob"), "DeepSeek compact alternate blob")
    benchmark_blob = _sha(benchmark.get("blob"), "DeepSeek benchmark alternate blob")

    declared_blobs = _manifest_mapping(manifest.get("engine_files"), "DeepSeek engine_files")
    workflow_blobs = _manifest_mapping(manifest.get("workflows"), "DeepSeek workflows")
    for path, blob in workflow_blobs.items():
        previous = declared_blobs.get(path)
        if previous is not None and previous != blob:
            raise ControlPlaneError(f"DeepSeek manifest declares conflicting blobs for {path}")
        declared_blobs[path] = blob
    explicit = {
        str(entrypoint): entrypoint_blob,
        str(meter_path): meter_blob,
        str(exact_qg_path): exact_qg_blob,
        str(qg_contract_path): qg_contract_blob,
        str(compact_path): compact_blob,
        str(benchmark_path): benchmark_blob,
    }
    for path, blob in explicit.items():
        previous = declared_blobs.get(path)
        if previous is not None and previous != blob:
            raise ControlPlaneError(f"DeepSeek manifest explicit/declared blob mismatch for {path}")
        declared_blobs[path] = blob

    files: dict[str, dict[str, Any]] = {}
    for path, expected_blob in sorted(declared_blobs.items()):
        evidence = _contents_metadata(DEEPSEEK_REPO, path, token, main_sha)
        if evidence["blob_sha"] != expected_blob:
            raise ControlPlaneError(
                f"DeepSeek STABLE manifest blob drift for {path}: "
                f"expected {expected_blob}, observed {evidence['blob_sha']}"
            )
        files[path] = evidence

    texts: dict[str, str] = {}
    for path in DEEPSEEK_PROJECTION_CONTRACT_FILES:
        evidence, text = _contents_file(DEEPSEEK_REPO, path, token, main_sha)
        expected_blob = declared_blobs.get(path)
        if expected_blob is None or evidence["blob_sha"] != expected_blob:
            raise ControlPlaneError(f"DeepSeek contract file {path} is not manifest-bound")
        texts[path] = text

    meter = texts["scripts/run_review_with_meter.py"]
    stable = texts["scripts/deepseek_reviewer_v2_1_1_entrypoint.py"]
    v21 = texts["scripts/deepseek_reviewer_v2_1_entrypoint.py"]
    exact_qg_text = texts["scripts/exact_qg_evidence.py"]
    review_workflow = texts[".github/workflows/deepseek-qore-review.yml"]
    auto_dispatch = texts[".github/workflows/deepseek-auto-dispatch.yml"]

    _require_markers(
        meter,
        "DeepSeek meter",
        (
            '"deepseek_reviewer_v2_1_1_entrypoint.py"',
            '"deepseek_reviewer_compact_budgeted_v20.py"',
            'os.environ.get("DEEPSEEK_REVIEWER_PROFILE", "stable")',
            'elif _REVIEWER_PROFILE == "compact-budgeted":',
            'elif _REVIEWER_PROFILE == "stable":',
            'startswith("BENCHMARK-COMPACT-")',
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
            "import exact_qg_evidence as exact_qg",
            "v21.v13.build_baseline_evidence = _build_baseline_with_exact_qg",
        ),
    )
    _require_markers(
        v21,
        "DeepSeek V2.1 reasoning contract",
        (
            '"DEEPSEEK_TOTAL_COMPLETION_TOKEN_BUDGET", "100000"',
            '"DEEPSEEK_VERDICT_RESERVE_TOKENS", "12000"',
            "thinking=True",
            "thinking=False",
            "v2_1_same_model_extractor=True",
            "v2_1_flash_substitution=False",
            "v2_1_cot_continuation=False",
            'if stage == "final-fallback":',
        ),
    )
    _require_markers(
        exact_qg_text,
        "DeepSeek stable exact-QG helper",
        (
            "_QG_EVIDENCE_MAX_CHARS = 8000",
            "EXPECTED_QG_SUMMARY_JSON",
            "authenticated_command_summaries",
            "_validate_checkout_synthetic",
            "Full command windows were parsed and validated internally",
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

    authorized: list[dict[str, Any]] = []
    for path in DEEPSEEK_AUTHORIZED_REVIEW_LANE_WORKFLOWS:
        evidence = files.get(path)
        if evidence is None:
            raise ControlPlaneError(f"DeepSeek stable manifest omits authorized review-lane workflow {path}")
        authorized.append(evidence)

    return {
        "bound_main_sha": main_sha,
        "authoritative_model": "deepseek-v4-pro",
        "governance_alignment": True,
        "stable_manifest": {
            **manifest_file,
            "path": manifest_path,
            "stable_manifest_count": 1,
            "profile_id": manifest["profile_id"],
            "status": manifest["status"],
            "model": manifest["model"],
            "entrypoint": entrypoint,
            "exact_blob_binding_verified": True,
        },
        "operational_default": {
            "profile": "stable",
            "entrypoint": entrypoint,
            "selection_source": meter_path,
            "manifest_governed": True,
        },
        "stable_contract": {
            "completion_budget_default": 100000,
            "verdict_reserve_default": 12000,
            "authoritative_analysis_thinking": True,
            "same_model_non_thinking_extractor": True,
            "flash_substitution": False,
            "cot_continuation": False,
            "complete_changed_and_dependency_evidence_preserved": True,
        },
        "alternate_profiles": {
            "compact-budgeted": {
                "entrypoint": compact_path,
                "ordinary_default": False,
                "activation": compact.get("activation"),
                "promoted_to_stable": False,
            },
            "benchmark-compact": {
                "entrypoint": benchmark_path,
                "ordinary_default": False,
                "activation": benchmark.get("activation"),
                "promoted_to_stable": False,
            },
        },
        "governance_resolution": {
            "stable_profile_recertified_and_live": True,
            "compact_v20_equivalence_or_stable_promotion": "ABSENT_AND_NOT_REQUIRED_FOR_ORDINARY_ROUTE",
            "ordinary_successor_requires_qore_governance": True,
        },
        "stable_profile_authorized_workflows": {
            "count": len(authorized),
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
            "stable_profile_bound": True,
        },
        "files": [files[path] for path in sorted(files)],
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
