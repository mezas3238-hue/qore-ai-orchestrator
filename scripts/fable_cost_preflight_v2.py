from __future__ import annotations

import math
from dataclasses import dataclass

from economic_control_plane import PriceCard


@dataclass(frozen=True, slots=True)
class FableAuditTokenPlanV2:
    stable_tokens: int
    changed_tokens: int
    cross_boundary_tokens: int
    expected_output_tokens: int
    cache_hit_ratio: float
    cache_write_ratio: float
    batch_discount: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "stable_tokens",
            "changed_tokens",
            "cross_boundary_tokens",
            "expected_output_tokens",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative exact int")
        for name in ("cache_hit_ratio", "cache_write_ratio", "batch_discount"):
            value = getattr(self, name)
            if isinstance(value, bool) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.cache_hit_ratio + self.cache_write_ratio > 1.0:
            raise ValueError("cache hit and write ratios cannot exceed the stable corpus")


@dataclass(frozen=True, slots=True)
class FableAuditCostV2:
    total_input_tokens: int
    cache_hit_tokens: int
    cache_write_tokens: int
    standard_input_tokens: int
    output_tokens: int
    pre_discount_usd: float
    estimated_usd: float
    hard_budget_usd: float
    within_budget: bool
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if self.hard_budget_usd < 0:
            raise ValueError("hard_budget_usd must be non-negative")
        if type(self.within_budget) is not bool:
            raise ValueError("within_budget must be exact bool")
        if type(self.shadow_only) is not bool or not self.shadow_only:
            raise ValueError("Fable V2 preflight must remain shadow-only until recertified")


def preflight_fable_cost_v2(
    *,
    token_plan: FableAuditTokenPlanV2,
    price_card: PriceCard,
    hard_budget_usd: float,
) -> FableAuditCostV2:
    """Cold/warm-cache-aware Fable cost preflight with no model or network call."""
    if hard_budget_usd < 0:
        raise ValueError("hard_budget_usd must be non-negative")

    cache_hit_tokens = math.floor(token_plan.stable_tokens * token_plan.cache_hit_ratio)
    cache_write_tokens = math.floor(token_plan.stable_tokens * token_plan.cache_write_ratio)
    standard_stable_tokens = (
        token_plan.stable_tokens - cache_hit_tokens - cache_write_tokens
    )
    standard_input_tokens = (
        standard_stable_tokens
        + token_plan.changed_tokens
        + token_plan.cross_boundary_tokens
    )
    total_input_tokens = (
        cache_hit_tokens + cache_write_tokens + standard_input_tokens
    )

    pre_discount_usd = (
        cache_hit_tokens * price_card.cached_input_per_million
        + cache_write_tokens * price_card.cache_write_per_million
        + standard_input_tokens * price_card.input_per_million
        + token_plan.expected_output_tokens * price_card.output_per_million
    ) / 1_000_000
    estimated_usd = pre_discount_usd * (1.0 - token_plan.batch_discount)

    return FableAuditCostV2(
        total_input_tokens=total_input_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_write_tokens=cache_write_tokens,
        standard_input_tokens=standard_input_tokens,
        output_tokens=token_plan.expected_output_tokens,
        pre_discount_usd=pre_discount_usd,
        estimated_usd=estimated_usd,
        hard_budget_usd=hard_budget_usd,
        within_budget=estimated_usd <= hard_budget_usd,
    )
