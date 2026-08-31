from __future__ import annotations

from typing import Any, Mapping

from economic_control_plane import sha256_json

ALLOWED_EXECUTION_MODES = {"SHADOW", "CONTROLLED_VALIDATION", "LIMITED_LIVE"}


def build_fable_audit_package_v2(
    *,
    shadow_package: Mapping[str, Any],
    execution_mode: str,
    activation_policy: Mapping[str, Any] | None,
    cost_preflight: Mapping[str, Any],
) -> dict[str, Any]:
    if shadow_package.get("schema_version") != "qore.fable.audit.package.v1":
        raise ValueError("unexpected source Fable package schema")
    if shadow_package.get("production_authority") is not False:
        raise ValueError("source Fable package attempted Production authority")
    if execution_mode not in ALLOWED_EXECUTION_MODES:
        raise ValueError("invalid Fable execution_mode")
    within_budget = cost_preflight.get("within_budget")
    estimated_usd = cost_preflight.get("estimated_usd")
    hard_budget_usd = cost_preflight.get("hard_budget_usd")
    if type(within_budget) is not bool or not isinstance(estimated_usd, (int, float)) or isinstance(estimated_usd, bool):
        raise ValueError("invalid Fable cost preflight evidence")
    if not isinstance(hard_budget_usd, (int, float)) or isinstance(hard_budget_usd, bool):
        raise ValueError("invalid Fable hard budget evidence")
    if execution_mode != "SHADOW" and not within_budget:
        raise ValueError("Fable execution is forbidden when deterministic cost preflight exceeds budget")
    if execution_mode == "LIMITED_LIVE":
        if not isinstance(activation_policy, Mapping) or activation_policy.get("mode") != "LIMITED_LIVE":
            raise ValueError("LIMITED_LIVE Fable package requires a passing activation policy")
        mode = str(shadow_package.get("audit_mode"))
        allowed_flag = {
            "DELTA": "delta_live",
            "CROSS_BOUNDARY": "cross_boundary_live",
            "FULL_SYSTEM": "full_system_live",
        }.get(mode)
        if allowed_flag is None or activation_policy.get(allowed_flag) is not True:
            raise ValueError("activation policy does not authorize this Fable audit mode")
        if activation_policy.get("reviewer_substitution") is not False:
            raise ValueError("Fable activation may not substitute mandatory reviewers")

    body = {
        key: value
        for key, value in shadow_package.items()
        if key not in {"shadow_only", "package_sha256", "package_id"}
    }
    body["schema_version"] = "qore.fable.audit.package.v2"
    body["execution_mode"] = execution_mode
    body["cost_preflight"] = dict(cost_preflight)
    body["reviewer_substitution"] = False
    body["production_authority"] = False
    digest = sha256_json(body)
    body["package_sha256"] = digest
    body["package_id"] = f"QORE-FABLE-AUDIT-V2-{digest[:24]}"
    return body
