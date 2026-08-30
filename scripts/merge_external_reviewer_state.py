#!/usr/bin/env python3
"""Attach existing reviewer-repository state to the canonical Sol snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--reviewers", required=True)
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    reviewers_path = Path(args.reviewers)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    reviewers = json.loads(reviewers_path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "qore.state.snapshot.v1":
        raise SystemExit("canonical QORE snapshot is invalid")
    if not isinstance(reviewers, dict) or reviewers.get("schema_version") != "qore.external.reviewers.v1":
        raise SystemExit("external reviewer state is invalid")
    snapshot["external_reviewer_state"] = reviewers
    snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "EXTERNAL_REVIEWER_STATE_MERGED configured={} errors={}".format(
            reviewers.get("configured"), len(reviewers.get("errors") or [])
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
