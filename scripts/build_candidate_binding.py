from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from economic_control_plane import CandidateIdentity


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _sha(name: str, value: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be an exact lowercase SHA-40")
    return value


def build_candidate_binding(
    *,
    root: Path,
    repository: str,
    base_sha: str,
    head_sha: str,
    synthetic_sha: str,
) -> dict[str, Any]:
    root = root.resolve()
    if not (root / ".git").exists():
        raise ValueError("root must be a local Git repository")
    if not repository.strip():
        raise ValueError("repository must be non-empty")
    base = _sha("base_sha", base_sha)
    head = _sha("head_sha", head_sha)
    synthetic = _sha("synthetic_sha", synthetic_sha)

    for name, sha in (("BASE", base), ("HEAD", head), ("SYNTHETIC", synthetic)):
        try:
            object_type = git(root, "cat-file", "-t", sha)
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"{name} object is not available locally") from exc
        if object_type != "commit":
            raise ValueError(f"{name} must resolve to a commit")

    try:
        subprocess.check_call(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", base, head],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError("BASE is not an ancestor of HEAD") from exc

    tree = _sha("tree_sha", git(root, "show", "-s", "--format=%T", head))
    parent_text = git(root, "show", "-s", "--format=%P", synthetic)
    parents = tuple(parent_text.split())
    if len(parents) != 2:
        raise ValueError("SYNTHETIC must have exactly two parents")
    if parents != (base, head):
        raise ValueError("SYNTHETIC parents must be exactly BASE then HEAD")

    synthetic_tree = _sha(
        "synthetic_tree_sha", git(root, "show", "-s", "--format=%T", synthetic)
    )
    candidate = CandidateIdentity(
        repository=repository,
        base_sha=base,
        head_sha=head,
        tree_sha=tree,
        synthetic_sha=synthetic,
        production_authority=False,
    )
    return {
        "schema_version": "qore.candidate.binding.v1",
        "candidate_id": candidate.candidate_id,
        "repository": repository,
        "base_sha": base,
        "head_sha": head,
        "tree_sha": tree,
        "synthetic_sha": synthetic,
        "synthetic_tree_sha": synthetic_tree,
        "synthetic_parents": list(parents),
        "base_is_ancestor_of_head": True,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build exact QORE candidate binding from local Git objects.")
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--synthetic", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_candidate_binding(
        root=args.repo_dir,
        repository=args.repository,
        base_sha=args.base,
        head_sha=args.head,
        synthetic_sha=args.synthetic,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
