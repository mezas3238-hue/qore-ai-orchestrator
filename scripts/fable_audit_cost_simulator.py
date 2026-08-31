from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from economic_control_plane import PriceCard
from fable_cost_preflight_v2 import FableAuditTokenPlanV2, preflight_fable_cost_v2


def _bounded_read(root: Path, relative: str) -> tuple[str, bytes]:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("audit corpus path must remain under root")
    root = root.resolve()
    resolved = (root / path).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("audit corpus path escapes root")
    if not resolved.is_file():
        raise ValueError(f"audit corpus file does not exist: {relative}")
    return path.as_posix(), resolved.read_bytes()


def _estimate_tokens(byte_count: int, chars_per_token: float) -> int:
    if isinstance(chars_per_token, bool) or chars_per_token <= 0:
        raise ValueError("chars_per_token must be positive")
    # UTF-8 bytes are a conservative deterministic proxy for corpus size.
    return int(round(byte_count / chars_per_token))


def _price_card(value: Mapping[str, Any]) -> PriceCard:
    return PriceCard(
        input_per_million=value["input_per_million"],
        cached_input_per_million=value["cached_input_per_million"],
        cache_write_per_million=value["cache_write_per_million"],
        output_per_million=value["output_per_million"],
    )


def simulate_fable_audit_cost(
    *,
    root: Path,
    stable_paths: Sequence[str],
    changed_paths: Sequence[str],
    cross_boundary_paths: Sequence[str],
    repository_paths: Sequence[str],
    expected_output_tokens: int,
    cache_hit_ratio: float,
    cache_write_ratio: float,
    batch_discount: float,
    price_card: PriceCard,
    hard_budget_usd: float,
    chars_per_token: float = 4.0,
) -> dict[str, Any]:
    categories = {
        "stable": tuple(stable_paths),
        "changed": tuple(changed_paths),
        "cross_boundary": tuple(cross_boundary_paths),
        "repository": tuple(repository_paths),
    }
    digest_bytes: dict[str, int] = {}
    manifest: list[dict[str, Any]] = []
    category_digests: dict[str, set[str]] = {key: set() for key in categories}

    for category, paths in categories.items():
        for relative in sorted(set(paths)):
            name, content = _bounded_read(root, relative)
            digest = hashlib.sha256(content).hexdigest()
            digest_bytes.setdefault(digest, len(content))
            category_digests[category].add(digest)
            manifest.append(
                {
                    "category": category,
                    "path": name,
                    "sha256": digest,
                    "bytes": len(content),
                }
            )

    def category_bytes(category: str) -> int:
        return sum(digest_bytes[digest] for digest in category_digests[category])

    stable_digests = category_digests["stable"]
    changed_digests = category_digests["changed"] - stable_digests
    cross_digests = (
        category_digests["cross_boundary"] - stable_digests - changed_digests
    )
    relevant_digests = stable_digests | changed_digests | cross_digests
    repository_digests = category_digests["repository"]

    stable_bytes = sum(digest_bytes[digest] for digest in stable_digests)
    changed_bytes = sum(digest_bytes[digest] for digest in changed_digests)
    cross_bytes = sum(digest_bytes[digest] for digest in cross_digests)
    relevant_bytes = sum(digest_bytes[digest] for digest in relevant_digests)
    repository_bytes = sum(digest_bytes[digest] for digest in repository_digests)

    plan = FableAuditTokenPlanV2(
        stable_tokens=_estimate_tokens(stable_bytes, chars_per_token),
        changed_tokens=_estimate_tokens(changed_bytes, chars_per_token),
        cross_boundary_tokens=_estimate_tokens(cross_bytes, chars_per_token),
        expected_output_tokens=expected_output_tokens,
        cache_hit_ratio=cache_hit_ratio,
        cache_write_ratio=cache_write_ratio,
        batch_discount=batch_discount,
    )
    estimate = preflight_fable_cost_v2(
        token_plan=plan,
        price_card=price_card,
        hard_budget_usd=hard_budget_usd,
    )

    total_manifest_bytes = sum(row["bytes"] for row in manifest)
    unique_manifest_bytes = sum(digest_bytes.values())
    return {
        "schema_version": "qore.fable.audit.cost.simulator.v1",
        "repository_unique_bytes": repository_bytes,
        "repository_estimated_tokens": _estimate_tokens(repository_bytes, chars_per_token),
        "unique_relevant_bytes": relevant_bytes,
        "unique_relevant_estimated_tokens": _estimate_tokens(relevant_bytes, chars_per_token),
        "stable_cacheable_bytes": stable_bytes,
        "stable_cacheable_estimated_tokens": plan.stable_tokens,
        "changed_unique_bytes": changed_bytes,
        "changed_estimated_tokens": plan.changed_tokens,
        "cross_boundary_unique_bytes": cross_bytes,
        "cross_boundary_estimated_tokens": plan.cross_boundary_tokens,
        "manifest_total_bytes_with_duplication": total_manifest_bytes,
        "manifest_unique_bytes": unique_manifest_bytes,
        "manifest_duplication_ratio": (
            1.0 - unique_manifest_bytes / total_manifest_bytes
            if total_manifest_bytes
            else 0.0
        ),
        "chars_per_token_assumption": chars_per_token,
        "token_plan": asdict(plan),
        "cost": asdict(estimate),
        "within_budget": estimate.within_budget,
        "manifest": sorted(manifest, key=lambda row: (row["category"], row["path"])),
        "policy": "SIMULATE_BEFORE_DISPATCH;NO_MODEL_CALL;NO_PRODUCTION_AUTHORITY",
        "shadow_only": True,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate Fable audit token/cost envelope from local corpus.")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.plan.read_text(encoding="utf-8"))
    result = simulate_fable_audit_cost(
        root=args.root,
        stable_paths=value.get("stable_paths", []),
        changed_paths=value.get("changed_paths", []),
        cross_boundary_paths=value.get("cross_boundary_paths", []),
        repository_paths=value.get("repository_paths", []),
        expected_output_tokens=value["expected_output_tokens"],
        cache_hit_ratio=value["cache_hit_ratio"],
        cache_write_ratio=value["cache_write_ratio"],
        batch_discount=value.get("batch_discount", 0.0),
        price_card=_price_card(value["price_card"]),
        hard_budget_usd=value["hard_budget_usd"],
        chars_per_token=value.get("chars_per_token", 4.0),
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
