from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from economic_control_plane import CostEvent, sha256_json


SUPPORTED_KINDS = frozenset({"SOL", "CODEX", "DEEPSEEK", "CLAUDE"})


def _nonnegative_int(value: Any, *, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact int")
    return value


def _optional_usd(payload: Mapping[str, Any]) -> float | None:
    direct = payload.get("observed_usd", payload.get("total_cost_usd"))
    if direct is not None:
        if isinstance(direct, bool) or not isinstance(direct, (int, float)) or direct < 0:
            raise ValueError("observed USD must be non-negative")
        return float(direct)
    currencies = payload.get("spent_by_currency")
    if isinstance(currencies, Mapping):
        usd = currencies.get("USD")
        if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= 0:
            return float(usd)
    return None


def _usage_view(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    usage = payload.get("usage")
    return usage if isinstance(usage, Mapping) else payload


def normalize_usage(
    *,
    kind: str,
    payload: Mapping[str, Any],
    session_id: str,
    candidate_id: str,
    stage: str,
) -> CostEvent:
    kind = kind.upper()
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"unsupported usage artifact kind: {kind}")
    usage = _usage_view(payload)

    if kind in {"SOL", "CODEX"}:
        model = str(usage["model"])
        input_tokens = _nonnegative_int(usage.get("input_tokens", 0), name="input_tokens")
        cached = _nonnegative_int(usage.get("cached_tokens", 0), name="cached_tokens")
        cache_write = _nonnegative_int(
            usage.get("cache_write_tokens", 0), name="cache_write_tokens"
        )
        output = _nonnegative_int(usage.get("output_tokens", 0), name="output_tokens")
        observed = _optional_usd(payload)
    elif kind == "DEEPSEEK":
        model = str(payload.get("model", usage.get("model", "deepseek")))
        hit = _nonnegative_int(
            usage.get("prompt_cache_hit_tokens", 0), name="prompt_cache_hit_tokens"
        )
        miss = _nonnegative_int(
            usage.get("prompt_cache_miss_tokens", 0), name="prompt_cache_miss_tokens"
        )
        prompt = usage.get("prompt_tokens")
        if prompt is None:
            input_tokens = hit + miss
        else:
            input_tokens = _nonnegative_int(prompt, name="prompt_tokens")
            if input_tokens < hit:
                raise ValueError("prompt_tokens cannot be less than cache-hit tokens")
        cached = hit
        cache_write = 0
        output = _nonnegative_int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)),
            name="completion_tokens",
        )
        observed = _optional_usd(payload)
    else:
        model = str(payload.get("model", usage.get("model", "claude")))
        direct_input = _nonnegative_int(
            usage.get("inputTokens", usage.get("input_tokens", 0)), name="inputTokens"
        )
        cached = _nonnegative_int(
            usage.get("cacheReadInputTokens", usage.get("cache_read_input_tokens", 0)),
            name="cacheReadInputTokens",
        )
        cache_write = _nonnegative_int(
            usage.get(
                "cacheCreationInputTokens", usage.get("cache_creation_input_tokens", 0)
            ),
            name="cacheCreationInputTokens",
        )
        input_tokens = direct_input + cached
        output = _nonnegative_int(
            usage.get("outputTokens", usage.get("output_tokens", 0)), name="outputTokens"
        )
        observed = _optional_usd(payload)

    if cached > input_tokens:
        raise ValueError("cached input cannot exceed total input")
    return CostEvent(
        session_id=session_id,
        actor=kind,
        model=model,
        stage=stage,
        candidate_id=candidate_id,
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=output,
        observed_usd=observed,
    )


def normalize_manifest(manifest: Mapping[str, Any], *, base_dir: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for item in manifest.get("artifacts", []):
        if not isinstance(item, Mapping):
            raise ValueError("artifact manifest entries must be objects")
        relative = Path(str(item["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact paths must remain inside base_dir")
        path = base_dir / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        event = normalize_usage(
            kind=str(item["kind"]),
            payload=payload,
            session_id=str(item["session_id"]),
            candidate_id=str(item["candidate_id"]),
            stage=str(item["stage"]),
        )
        events.append(asdict(event))
        sources.append(
            {
                "path": relative.as_posix(),
                "kind": str(item["kind"]).upper(),
                "sha256": sha256_json(payload),
                "event_id": event.event_id,
            }
        )
    return {
        "schema_version": "qore.ai.usage.normalized.v1",
        "events": events,
        "sources": sources,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize QORE AI usage artifacts without model calls.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--base-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    normalized = normalize_manifest(manifest, base_dir=args.base_dir.resolve())
    args.output.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
