from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def _bounded_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("source path must remain under the declared root")
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("source path escapes declared root")
    return resolved


def _symbol_nodes(tree: ast.AST) -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}

    def visit_body(body: Sequence[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}.{node.name}" if prefix else node.name
                if name in found:
                    raise ValueError(f"duplicate symbol identity: {name}")
                found[name] = node
                if isinstance(node, ast.ClassDef):
                    visit_body(node.body, name)

    if not isinstance(tree, ast.Module):
        raise ValueError("expected Python module AST")
    visit_body(tree.body)
    return found


def slice_python_symbol(*, root: Path, relative_path: str, symbol: str) -> dict[str, Any]:
    if not symbol or not isinstance(symbol, str):
        raise ValueError("symbol must be a non-empty string")
    path = _bounded_path(root, relative_path)
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=relative_path)
    node = _symbol_nodes(tree).get(symbol)
    if node is None:
        raise ValueError(f"symbol not found: {symbol}")
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if type(start) is not int or type(end) is not int or start <= 0 or end < start:
        raise ValueError("symbol has invalid source range")
    lines = text.splitlines(keepends=True)
    content = "".join(lines[start - 1 : end])
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    file_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "schema_version": "qore.source.symbol.slice.v1",
        "path": Path(relative_path).as_posix(),
        "symbol": symbol,
        "start_line": start,
        "end_line": end,
        "file_sha256": file_digest,
        "slice_sha256": digest,
        "content": content,
    }


def build_slice_package(
    *,
    root: Path,
    requests: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    slices = [
        slice_python_symbol(
            root=root,
            relative_path=str(request["path"]),
            symbol=str(request["symbol"]),
        )
        for request in requests
    ]
    canonical = json.dumps(slices, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "schema_version": "qore.source.slice.package.v1",
        "slices": slices,
        "package_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic Python source slices.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--requests", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    requests = json.loads(args.requests.read_text(encoding="utf-8"))
    if not isinstance(requests, list):
        raise SystemExit("requests must be a JSON array")
    package = build_slice_package(root=args.root, requests=requests)
    args.output.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
