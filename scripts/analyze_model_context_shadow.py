from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from economic_control_plane import context_duplication_ratio


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def analyze_model_context(model_context: Mapping[str, Any]) -> dict[str, Any]:
    if model_context.get("schema_version") != "qore.model.context.v1":
        raise ValueError("unexpected model context schema")
    stable = model_context.get("stable_context")
    dynamic = model_context.get("dynamic_context")
    engineer = model_context.get("engineer_context")
    if not isinstance(stable, Mapping) or not isinstance(dynamic, Mapping) or not isinstance(engineer, Mapping):
        raise ValueError("model context sections must be objects")

    stable_text = _compact(stable)
    dynamic_text = _compact(dynamic)
    engineer_text = _compact(engineer)
    architect_text = _compact({"stable_context": stable, "dynamic_context": dynamic})

    section_rows: list[dict[str, Any]] = []
    for scope, section in (("stable", stable), ("dynamic", dynamic), ("engineer", engineer)):
        for key, value in section.items():
            text = _compact(value)
            section_rows.append(
                {
                    "scope": scope,
                    "section": str(key),
                    "chars": len(text),
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    section_rows.sort(key=lambda row: (-row["chars"], row["scope"], row["section"]))

    duplication = context_duplication_ratio(
        {
            "stable": stable_text,
            "dynamic": dynamic_text,
            "engineer": engineer_text,
        },
        chunk_chars=4096,
    )
    stable_digest = hashlib.sha256(stable_text.encode("utf-8")).hexdigest()
    return {
        "schema_version": "qore.model.context.shadow.analysis.v1",
        "main_sha": model_context.get("main_sha"),
        "full_snapshot_sha256": model_context.get("full_snapshot_sha256"),
        "architect_context_chars": len(architect_text),
        "stable_context_chars": len(stable_text),
        "dynamic_context_chars": len(dynamic_text),
        "engineer_context_chars": len(engineer_text),
        "stable_share_of_architect_context": (
            len(stable_text) / len(architect_text) if architect_text else 0.0
        ),
        "cross_section_duplication_ratio": duplication,
        "stable_prefix_sha256": stable_digest,
        "shadow_prompt_cache_key": f"qore-sol-stable-v2-{stable_digest[:16]}",
        "largest_sections": section_rows[:12],
        "policy": "MEASURE_BEFORE_COMPACTING;DO_NOT_REMOVE_SEMANTIC_EVIDENCE_BY_SIZE_ALONE",
        "shadow_only": True,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze existing QORE model context without a model call.")
    parser.add_argument("--model-context", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    context = json.loads(args.model_context.read_text(encoding="utf-8"))
    result = analyze_model_context(context)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
