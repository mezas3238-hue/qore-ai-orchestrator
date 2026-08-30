#!/usr/bin/env python3
"""Add bounded reviewer control-plane evidence to the Codex engineering context.

Sol receives the complete bounded reviewer projection. Codex receives only the
operational subset required to avoid duplicate work and understand current
reviewer bindings. Historical bodies, review prose and reviewer implementation
internals remain architect-only evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

MAX_ENGINEER_CONTEXT_CHARS = 70000
MAX_ENGINEER_REVIEWER_RUNS = 5


def compact_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source.get(key) for key in keys if key in source}


def _compact_run(value: Any) -> dict[str, Any]:
    return _pick(
        value,
        (
            "id",
            "name",
            "display_title",
            "event",
            "status",
            "conclusion",
            "head_branch",
            "head_sha",
            "run_attempt",
            "created_at",
            "updated_at",
        ),
    )


def _compact_pull(value: Any) -> dict[str, Any]:
    result = _pick(
        value,
        (
            "number",
            "title",
            "state",
            "draft",
            "updated_at",
            "base_ref",
            "base_sha",
            "head_ref",
            "head_sha",
        ),
    )
    if isinstance(value, dict) and isinstance(value.get("latest_head_run"), dict):
        result["latest_head_run"] = _compact_run(value["latest_head_run"])
    return result


def _compact_issue(value: Any) -> dict[str, Any]:
    return _pick(value, ("number", "title", "state", "updated_at", "labels"))


def _compact_technical_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    # Keep the exact operational identity and safety contract, not implementation
    # file inventories or historical authorized-workflow lists. Those remain in
    # Sol's architect context.
    return {
        key: value[key]
        for key in (
            "bound_main_sha",
            "authoritative_model",
            "operational_default",
            "binding_contract",
            "qg_transport_contract",
            "stable_fallback",
        )
        if key in value
    }


def _compact_control_plane(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _pick(value, ("repository", "visibility", "main"))
    pulls = value.get("open_pull_requests")
    issues = value.get("open_issues")
    runs = value.get("recent_action_runs")
    if isinstance(pulls, list):
        result["open_pull_requests"] = [
            _compact_pull(item) for item in pulls if isinstance(item, dict)
        ]
    if isinstance(issues, list):
        result["open_issues"] = [
            _compact_issue(item) for item in issues if isinstance(item, dict)
        ]
    if isinstance(runs, list):
        result["recent_action_runs"] = [
            _compact_run(item)
            for item in runs[:MAX_ENGINEER_REVIEWER_RUNS]
            if isinstance(item, dict)
        ]
    projection = _compact_technical_projection(value.get("technical_projection"))
    if projection:
        result["technical_projection"] = projection
    return result


def compact_external_for_engineer(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = _pick(value, ("schema_version", "configured", "errors"))
    for reviewer_name in ("claude", "deepseek"):
        reviewer = value.get(reviewer_name)
        if not isinstance(reviewer, dict):
            continue
        compact = _pick(
            reviewer,
            ("repository", "status", "current_request", "result_source"),
        )
        control = _compact_control_plane(reviewer.get("control_plane"))
        if control:
            compact["control_plane"] = control
        artifact = _pick(
            reviewer.get("artifact"),
            ("id", "name", "created_at", "expires_at", "digest"),
        )
        if artifact:
            compact["artifact"] = artifact
        review = reviewer.get("review")
        if isinstance(review, dict) and "verdict" in review:
            compact["review"] = {"verdict": review.get("verdict")}
        result[reviewer_name] = compact
    return result


def augment(context: dict[str, Any]) -> dict[str, Any]:
    dynamic = context.get("dynamic_context")
    engineer = context.get("engineer_context")
    if not isinstance(dynamic, dict) or not isinstance(engineer, dict):
        raise ValueError("model context lacks dynamic/engineer sections")
    external = dynamic.get("external_reviewer_state")
    if isinstance(external, dict):
        engineer["external_reviewer_state"] = compact_external_for_engineer(external)
    chars = compact_chars(engineer)
    if chars > MAX_ENGINEER_CONTEXT_CHARS:
        raise ValueError(
            f"engineer context exceeds bound after reviewer evidence: {chars} > {MAX_ENGINEER_CONTEXT_CHARS}"
        )
    metrics = context.get("metrics")
    if isinstance(metrics, dict):
        metrics["engineer_context_chars"] = chars
    return context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        raise SystemExit("model context must be an object")
    try:
        augmented = augment(context)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    output = Path(args.output)
    output.write_text(json.dumps(augmented, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "ENGINEER_CONTEXT_AUGMENTED chars={}".format(
            (augmented.get("metrics") or {}).get("engineer_context_chars")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
