# CODEX V5 — Terminal deterministic Quality Gate at model-budget edge

## Scope

Codex Worker V5 preserves the existing bounded worker contract:

- model: GPT-5.3-Codex;
- `MAX_TURNS = 16`;
- `MAX_TOTAL_TOKENS = 120000` input-price-equivalent units;
- `store = false` and the V4 prompt-cache contract;
- no arbitrary shell, GitHub credential, network tool, merge/review authority, or Production authority for the model;
- V4 exact historical-candidate materialization remains unchanged.

V5 does **not** increase a model budget or make a hidden retry.

## Problem closed

A real V4 worker reached its budget immediately after a valid `apply_patch`. The candidate existed in the isolated workspace, but V3/V4 converted the result to `BLOCKED` before the model could spend another turn on `run_quality_gate`. The workspace then disappeared without an engineering candidate even though the remaining operation was deterministic and already controller-owned.

## Terminal-QG rule

Policy identifier:

`budget-after-successful-apply-patch-controller-qg-v1`

The fallback is eligible only when all of the following are true:

1. the model budget boundary invokes the existing budget-block path;
2. the **immediately preceding bounded tool action** is a successfully applied `apply_patch`;
3. the current candidate fingerprint is byte-identical to the fingerprint recorded immediately after that patch;
4. the candidate is non-empty.

Any intervening read, search, diff, targeted test, QG action, failed tool, or rejected patch removes eligibility. The hard turn-limit path is not changed and cannot use this fallback.

When eligible, the controller makes **zero additional model calls** and executes the immutable full QORE gate:

- `ruff check .`
- `mypy src tests`
- `pytest --cov=src/qore --cov-report=term-missing`

The candidate fingerprint is measured again after the gate.

### Green and byte-stable

If all three commands succeed and the candidate fingerprint is unchanged, V5 emits `READY` only in the engineering-candidate sense. The normal workflow then independently reruns Ruff, Mypy, and Pytest/coverage before publication. Sol and the independent reviewer sequence still adjudicate semantic completeness. `READY` is not merge approval, Production readiness, or real-capital authority.

### Any failure or mutation

If the terminal QG fails, raises an execution error, or changes the candidate fingerprint, the worker remains `BLOCKED`; no candidate is published as READY.

## Evidence hygiene

The usage artifact records bounded terminal-QG evidence:

- policy and trigger;
- no-additional-model-call flag;
- before/after candidate fingerprints;
- changed filenames;
- command names and return codes;
- QG success/run number;
- `production_authority: false`.

Raw QG output is deliberately excluded from this evidence.

## Permanent boundaries

This change does not modify qore-core, lower tests, suppress failures, change reviewer order, bypass Risk, authorize Production, or permit real-money execution. It removes one deterministic waste mode while preserving the existing fail-closed budget protector.
