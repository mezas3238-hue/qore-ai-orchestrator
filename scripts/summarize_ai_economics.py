from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from economic_control_plane import CostEvent, PriceCard


def _event(value: Mapping[str, Any]) -> CostEvent:
    return CostEvent(
        session_id=value["session_id"],
        actor=value["actor"],
        model=value["model"],
        stage=value["stage"],
        candidate_id=value["candidate_id"],
        input_tokens=value.get("input_tokens", 0),
        cached_input_tokens=value.get("cached_input_tokens", 0),
        cache_write_tokens=value.get("cache_write_tokens", 0),
        output_tokens=value.get("output_tokens", 0),
        observed_usd=value.get("observed_usd"),
    )


def _card(value: Mapping[str, Any]) -> PriceCard:
    return PriceCard(
        input_per_million=value["input_per_million"],
        cached_input_per_million=value["cached_input_per_million"],
        cache_write_per_million=value.get("cache_write_per_million", 0.0),
        output_per_million=value["output_per_million"],
    )


def summarize(
    *,
    events: Sequence[Mapping[str, Any]],
    price_cards: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    cards = {model: _card(value) for model, value in price_cards.items()}
    groups: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "cache_write_tokens": 0,
            "output_tokens": 0,
            "estimated_or_observed_usd": 0.0,
        }
    )
    total_usd = 0.0
    total_input = 0
    total_cached = 0
    total_cache_write = 0
    total_output = 0

    for raw in events:
        event = _event(raw)
        key = (event.actor, event.model, event.stage)
        group = groups[key]
        group["calls"] += 1
        group["input_tokens"] += event.input_tokens
        group["cached_input_tokens"] += event.cached_input_tokens
        group["cache_write_tokens"] += event.cache_write_tokens
        group["output_tokens"] += event.output_tokens
        if event.observed_usd is not None:
            cost = event.observed_usd
        else:
            card = cards.get(event.model)
            if card is None:
                raise KeyError(f"missing price card for {event.model}")
            cost = card.estimate(event)
        group["estimated_or_observed_usd"] += cost
        total_usd += cost
        total_input += event.input_tokens
        total_cached += event.cached_input_tokens
        total_cache_write += event.cache_write_tokens
        total_output += event.output_tokens

    rows = []
    for (actor, model, stage), metrics in sorted(groups.items()):
        row = {"actor": actor, "model": model, "stage": stage, **metrics}
        row["cache_read_ratio"] = (
            metrics["cached_input_tokens"] / metrics["input_tokens"]
            if metrics["input_tokens"]
            else 0.0
        )
        rows.append(row)

    return {
        "schema_version": "qore.ai.economics.summary.v1",
        "totals": {
            "calls": sum(row["calls"] for row in rows),
            "input_tokens": total_input,
            "cached_input_tokens": total_cached,
            "cache_write_tokens": total_cache_write,
            "output_tokens": total_output,
            "cache_read_ratio": (total_cached / total_input) if total_input else 0.0,
            "estimated_or_observed_usd": total_usd,
        },
        "by_actor_model_stage": rows,
        "production_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize normalized QORE AI economics.")
    parser.add_argument("--normalized", required=True, type=Path)
    parser.add_argument("--price-cards", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    normalized = json.loads(args.normalized.read_text(encoding="utf-8"))
    cards = json.loads(args.price_cards.read_text(encoding="utf-8"))
    result = summarize(events=normalized.get("events", []), price_cards=cards)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
