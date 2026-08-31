from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


VALID_ADJUDICATIONS = frozenset({"VALID", "FALSE_POSITIVE", "DUPLICATE", "UNRESOLVED"})


def analyze_reviewer_economics(
    findings: Sequence[Mapping[str, Any]],
    reviewer_costs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    cost_by_reviewer: dict[str, float] = defaultdict(float)
    calls_by_reviewer: dict[str, int] = defaultdict(int)
    for row in reviewer_costs:
        reviewer = str(row.get("reviewer", "")).strip()
        if not reviewer:
            raise ValueError("reviewer cost requires reviewer")
        cost = row.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise ValueError("cost_usd must be non-negative")
        cost_by_reviewer[reviewer] += float(cost)
        calls_by_reviewer[reviewer] += 1

    rows_by_reviewer: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "findings": 0,
            "valid_material_findings": 0,
            "unique_valid_material_findings": 0,
            "duplicate_findings": 0,
            "false_positives": 0,
            "unresolved": 0,
            "severity": defaultdict(int),
        }
    )
    valid_fingerprints: dict[str, set[str]] = defaultdict(set)
    owners_by_fingerprint: dict[str, set[str]] = defaultdict(set)

    normalized: list[tuple[str, str, str, bool, str, str]] = []
    for finding in findings:
        reviewer = str(finding.get("reviewer", "")).strip()
        finding_id = str(finding.get("finding_id", "")).strip()
        fingerprint = str(finding.get("fingerprint", "")).strip()
        adjudication = str(finding.get("adjudication", "")).strip().upper()
        severity = str(finding.get("severity", "UNKNOWN")).strip().upper() or "UNKNOWN"
        material = finding.get("material")
        if not reviewer or not finding_id or not fingerprint:
            raise ValueError("finding requires reviewer, finding_id and fingerprint")
        if adjudication not in VALID_ADJUDICATIONS:
            raise ValueError("finding adjudication is invalid")
        if type(material) is not bool:
            raise ValueError("finding material must be exact bool")
        normalized.append((reviewer, finding_id, fingerprint, material, adjudication, severity))
        if adjudication == "VALID" and material:
            owners_by_fingerprint[fingerprint].add(reviewer)

    for reviewer, _, fingerprint, material, adjudication, severity in normalized:
        row = rows_by_reviewer[reviewer]
        row["findings"] += 1
        row["severity"][severity] += 1
        if adjudication == "VALID" and material:
            row["valid_material_findings"] += 1
            valid_fingerprints[reviewer].add(fingerprint)
        elif adjudication == "DUPLICATE":
            row["duplicate_findings"] += 1
        elif adjudication == "FALSE_POSITIVE":
            row["false_positives"] += 1
        elif adjudication == "UNRESOLVED":
            row["unresolved"] += 1

    all_reviewers = sorted(
        set(rows_by_reviewer) | set(cost_by_reviewer) | set(calls_by_reviewer)
    )
    output_rows: list[dict[str, Any]] = []
    for reviewer in all_reviewers:
        stats = rows_by_reviewer[reviewer]
        unique_owned = {
            fingerprint
            for fingerprint in valid_fingerprints[reviewer]
            if owners_by_fingerprint[fingerprint] == {reviewer}
        }
        stats["unique_valid_material_findings"] = len(unique_owned)
        cost = cost_by_reviewer[reviewer]
        unique_count = len(unique_owned)
        valid_count = len(valid_fingerprints[reviewer])
        output_rows.append(
            {
                "reviewer": reviewer,
                "calls": calls_by_reviewer[reviewer],
                "cost_usd": cost,
                "findings": stats["findings"],
                "valid_material_findings": stats["valid_material_findings"],
                "distinct_valid_material_fingerprints": valid_count,
                "unique_valid_material_findings": unique_count,
                "duplicate_findings": stats["duplicate_findings"],
                "false_positives": stats["false_positives"],
                "unresolved": stats["unresolved"],
                "severity": dict(sorted(stats["severity"].items())),
                "cost_per_distinct_valid_material_finding": (
                    cost / valid_count if valid_count else None
                ),
                "cost_per_unique_valid_material_finding": (
                    cost / unique_count if unique_count else None
                ),
            }
        )

    overlap = sorted(
        {
            fingerprint: sorted(reviewers)
            for fingerprint, reviewers in owners_by_fingerprint.items()
            if len(reviewers) > 1
        }.items()
    )
    total_cost = sum(cost_by_reviewer.values())
    return {
        "schema_version": "qore.reviewer.economics.v1",
        "reviewers": output_rows,
        "cross_reviewer_overlap": [
            {"fingerprint": fingerprint, "reviewers": reviewers}
            for fingerprint, reviewers in overlap
        ],
        "total_cost_usd": total_cost,
        "policy": "MEASURE_MARGINAL_MATERIAL_DEFECT_DETECTION_PER_DOLLAR;DO_NOT_SUPPRESS_REVIEWERS",
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure independent reviewer economics without changing policy.")
    parser.add_argument("--findings", required=True, type=Path)
    parser.add_argument("--costs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    findings = json.loads(args.findings.read_text(encoding="utf-8"))
    costs = json.loads(args.costs.read_text(encoding="utf-8"))
    if not isinstance(findings, list) or not isinstance(costs, list):
        raise SystemExit("findings and costs must be JSON arrays")
    result = analyze_reviewer_economics(findings, costs)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
