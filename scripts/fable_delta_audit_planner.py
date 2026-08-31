from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from build_static_code_index import reverse_impact_closure


def _manifest_map(rows: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        path = str(row.get("path", "")).strip()
        digest = str(row.get("sha256", "")).strip()
        if not path or len(digest) != 64:
            raise ValueError(f"{label} manifest rows require path and sha256")
        if path in result and result[path] != digest:
            raise ValueError(f"{label} manifest contains conflicting digest for {path}")
        result[path] = digest
    return result


def plan_delta_audit(
    *,
    previous_manifest: Sequence[Mapping[str, Any]],
    current_index: Mapping[str, Any],
    forced_cross_boundary_paths: Sequence[str],
) -> dict[str, Any]:
    if current_index.get("schema_version") != "qore.static.code.index.v1":
        raise ValueError("unexpected current static index schema")
    previous = _manifest_map(previous_manifest, label="previous")
    current = _manifest_map(current_index.get("files", []), label="current")

    previous_paths = set(previous)
    current_paths = set(current)
    added = sorted(current_paths - previous_paths)
    removed = sorted(previous_paths - current_paths)
    modified = sorted(
        path for path in previous_paths & current_paths if previous[path] != current[path]
    )
    changed = sorted(set(added) | set(modified))

    edges = current_index.get("local_dependency_edges", [])
    if not isinstance(edges, list):
        raise ValueError("local_dependency_edges must be an array")
    impact = set(
        reverse_impact_closure(
            changed_paths=changed,
            local_dependency_edges=edges,
        )
    )
    forced = {str(path) for path in forced_cross_boundary_paths}
    missing_forced = sorted(path for path in forced if path not in current_paths)
    if missing_forced:
        raise ValueError(
            "forced cross-boundary paths must exist in current index: "
            + ", ".join(missing_forced)
        )
    impact.update(forced)

    changed_set = set(changed)
    transitively_impacted = sorted(impact - changed_set)
    unchanged = current_paths - changed_set
    unchanged_isolated = sorted(unchanged - set(transitively_impacted))

    return {
        "schema_version": "qore.fable.delta.audit.plan.v1",
        "added": added,
        "modified": modified,
        "removed": removed,
        "changed": changed,
        "transitively_impacted": transitively_impacted,
        "forced_cross_boundary": sorted(forced),
        "unchanged_isolated": unchanged_isolated,
        "fresh_audit_paths": sorted(changed_set | set(transitively_impacted)),
        "prior_evidence_reference_only_paths": unchanged_isolated,
        "rules": [
            "unchanged isolated blobs may contribute prior factual evidence only",
            "semantic approval is never transferred by this plan",
            "dynamic imports/runtime DI require separate evidence",
            "periodic full-system recertification is not waived",
        ],
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan an incremental Fable red-team corpus deterministically.")
    parser.add_argument("--previous-manifest", required=True, type=Path)
    parser.add_argument("--current-index", required=True, type=Path)
    parser.add_argument("--forced-cross-boundary", nargs="*", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    previous = json.loads(args.previous_manifest.read_text(encoding="utf-8"))
    current = json.loads(args.current_index.read_text(encoding="utf-8"))
    if not isinstance(previous, list):
        raise SystemExit("previous manifest must be a JSON array")
    result = plan_delta_audit(
        previous_manifest=previous,
        current_index=current,
        forced_cross_boundary_paths=args.forced_cross_boundary,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
