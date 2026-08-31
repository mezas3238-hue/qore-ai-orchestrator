from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


def _bounded_read(root: Path, relative: str) -> tuple[str, str]:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("stable-prefix path must remain under root")
    root = root.resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("stable-prefix path escapes root")
    return path.as_posix(), resolved.read_text(encoding="utf-8")


def build_stable_prefix(
    *,
    root: Path,
    role: str,
    contract_version: str,
    files: Sequence[str],
) -> dict[str, Any]:
    if not role or not isinstance(role, str):
        raise ValueError("role must be a non-empty string")
    if not contract_version or not isinstance(contract_version, str):
        raise ValueError("contract_version must be a non-empty string")
    if not files:
        raise ValueError("stable prefix requires at least one immutable source")

    sections: list[str] = []
    manifest: list[dict[str, Any]] = []
    for relative in files:
        name, content = _bounded_read(root, relative)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        manifest.append({"path": name, "sha256": digest, "chars": len(content)})
        sections.append(f"===== {name} =====\n{content.rstrip()}\n")

    prefix_text = "\n".join(sections)
    prefix_digest = hashlib.sha256(prefix_text.encode("utf-8")).hexdigest()
    cache_key = f"qore-{role.lower()}-{contract_version}-{prefix_digest[:16]}"
    return {
        "schema_version": "qore.stable.prompt.prefix.v1",
        "role": role,
        "contract_version": contract_version,
        "manifest": manifest,
        "prefix_text": prefix_text,
        "prefix_sha256": prefix_digest,
        "prefix_chars": len(prefix_text),
        "prompt_cache_key": cache_key,
        "mutation_policy": "APPEND_DYNAMIC_CONTEXT_AFTER_STABLE_PREFIX_ONLY",
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic stable prompt-cache prefix.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--role", required=True)
    parser.add_argument("--contract-version", required=True)
    parser.add_argument("--files", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_stable_prefix(
        root=args.root,
        role=args.role,
        contract_version=args.contract_version,
        files=args.files,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
