from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Sequence


PATTERNS = (
    ("PYTHON_SLEEP", re.compile(r"\b(?:time\.)?sleep\s*\(")),
    ("SHELL_SLEEP", re.compile(r"(?:^|\s)sleep\s+[0-9]")),
    ("INFINITE_LOOP", re.compile(r"\bwhile\s+(?:True|true|:)")),
    ("POLLING_TERM", re.compile(r"\bpoll(?:ing)?\b", re.IGNORECASE)),
)


def audit_paths(*, root: Path, paths: Sequence[str]) -> dict[str, Any]:
    root = root.resolve()
    hits: list[dict[str, Any]] = []
    for relative in sorted(paths):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("audit path must remain under root")
        resolved = (root / path).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError("audit path escapes root")
        text = resolved.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in PATTERNS:
                if pattern.search(line):
                    hits.append(
                        {
                            "path": path.as_posix(),
                            "line": line_no,
                            "kind": kind,
                            "line_sha256": __import__("hashlib").sha256(
                                line.encode("utf-8")
                            ).hexdigest(),
                        }
                    )
    return {
        "schema_version": "qore.polling.audit.v1",
        "scanned_files": len(paths),
        "hit_count": len(hits),
        "hits": hits,
        "interpretation": "EVIDENCE_ONLY_NOT_AUTOMATIC_DEFECT",
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit residual polling/sleep patterns deterministically.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--paths", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit_paths(root=args.root, paths=args.paths)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
