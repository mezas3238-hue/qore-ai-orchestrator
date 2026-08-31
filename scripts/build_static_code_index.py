from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


def _bounded(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("indexed path must remain under root")
    root = root.resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("indexed path escapes root")
    return resolved


def module_name(path: str) -> str | None:
    p = Path(path)
    if p.suffix != ".py":
        return None
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _symbols(tree: ast.AST) -> list[str]:
    result: list[str] = []
    if not isinstance(tree, ast.Module):
        return result
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            result.append(node.name)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result.append(f"{node.name}.{child.name}")
    return sorted(result)


def _imports(tree: ast.AST) -> list[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = "." * node.level
                result.add(prefix + (node.module or ""))
            elif node.module:
                result.add(node.module)
    return sorted(result)


def build_static_index(*, root: Path, paths: Sequence[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    module_to_path: dict[str, str] = {}
    for relative in sorted(set(paths)):
        resolved = _bounded(root, relative)
        if not resolved.is_file():
            raise ValueError(f"indexed file does not exist: {relative}")
        content = resolved.read_bytes()
        row: dict[str, Any] = {
            "path": Path(relative).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
            "language": "python" if resolved.suffix == ".py" else "other",
            "module": module_name(relative),
            "imports": [],
            "symbols": [],
        }
        if resolved.suffix == ".py":
            text = content.decode("utf-8")
            tree = ast.parse(text, filename=relative)
            row["imports"] = _imports(tree)
            row["symbols"] = _symbols(tree)
            if row["module"]:
                module_to_path[str(row["module"])] = row["path"]
        rows.append(row)

    local_edges: list[dict[str, str]] = []
    for row in rows:
        if row["language"] != "python":
            continue
        importer_module = row["module"]
        for imported in row["imports"]:
            resolved_module = _resolve_import(importer_module, imported)
            target = _best_local_target(resolved_module, module_to_path)
            if target is not None and target != row["path"]:
                local_edges.append({"from": row["path"], "to": target, "import": imported})

    local_edges.sort(key=lambda edge: (edge["from"], edge["to"], edge["import"]))
    canonical = json.dumps(
        {"files": rows, "local_dependency_edges": local_edges},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {
        "schema_version": "qore.static.code.index.v1",
        "files": rows,
        "local_dependency_edges": local_edges,
        "index_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "limitations": [
            "static imports only",
            "dynamic imports and runtime dependency injection require separate evidence",
            "dependency reachability is not semantic approval",
        ],
        "production_authority": False,
    }


def _resolve_import(importer_module: str | None, imported: str) -> str:
    if not imported.startswith("."):
        return imported
    if not importer_module:
        return imported.lstrip(".")
    level = len(imported) - len(imported.lstrip("."))
    suffix = imported[level:]
    parent = importer_module.split(".")[:-1]
    if level > 1:
        parent = parent[: max(0, len(parent) - (level - 1))]
    parts = parent + ([suffix] if suffix else [])
    return ".".join(part for part in parts if part)


def _best_local_target(imported: str, module_to_path: Mapping[str, str]) -> str | None:
    candidate = imported
    while candidate:
        if candidate in module_to_path:
            return module_to_path[candidate]
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return None


def reverse_impact_closure(
    *,
    changed_paths: Iterable[str],
    local_dependency_edges: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    impacted = {Path(path).as_posix() for path in changed_paths}
    reverse: dict[str, set[str]] = {}
    for edge in local_dependency_edges:
        source = str(edge["from"])
        target = str(edge["to"])
        reverse.setdefault(target, set()).add(source)

    queue = list(sorted(impacted))
    while queue:
        current = queue.pop(0)
        for dependent in sorted(reverse.get(current, ())):
            if dependent not in impacted:
                impacted.add(dependent)
                queue.append(dependent)
    return tuple(sorted(impacted))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic static source/dependency index.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--paths", required=True, nargs="+")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_static_index(root=args.root, paths=args.paths)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
