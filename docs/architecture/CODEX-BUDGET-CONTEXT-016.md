# CODEX-BUDGET-CONTEXT-016

## Purpose

Prevent bounded GPT-5.3-Codex engineering workers from terminating prematurely because repeated cached conversation prefixes are counted as if every token had full input cost, while also bounding repeated tool-output context growth.

## Observed live failure

Three autonomous Codex jobs against the same qore-core correction stream returned `BLOCKED` before producing a candidate. The third run recorded 124,147 raw input tokens, including 91,520 cached input tokens, plus 778 output tokens. The old controller compared raw cumulative `total_tokens` directly with `MAX_TOTAL_TOKENS=120000`, so cached prefix replay exhausted the worker cap despite materially lower spend-equivalent usage.

This was an infrastructure budget-accounting/context-retention defect. It was not evidence that qore-core was semantically blocked.

## Budget contract

`MAX_TOTAL_TOKENS` remains exactly `120000`. It is now interpreted as GPT-5.3-Codex normal-input-price-equivalent token units, matching the pinned controller pricing ratios:

- normal uncached input: `1.0x`;
- cached input: `0.1x`;
- cache-write reserve: `1.25x`;
- output (including reasoning output): `8.0x`.

The implementation uses integer arithmetic with denominator 20; no floating-point budget decisions are permitted. Invalid or overlapping usage counters fail closed.

Raw API counters are not discarded. The immutable usage artifact preserves `input_tokens`, `cached_tokens`, `cache_write_tokens`, `output_tokens`, `total_tokens`, plus `raw_total_tokens`, `budget_tokens`, `budget_formula_version`, and the unchanged `max_total_tokens`.

The change does not raise the 120k hard cap; it fixes what the cap measures so it reflects the model's pinned pricing contract rather than repeatedly charging cached prefixes at full price.

## Deterministic context compaction

The worker still manually carries Responses API reasoning and function-call items across turns. To prevent old repository/tool payloads from growing the repeated prefix indefinitely:

- the two most recent `function_call_output` items remain byte-for-byte available to the model;
- older tool outputs are replaced only in the model-facing projection by a deterministic record containing SHA-256, original character count, and an instruction to re-run the controller tool when exact content is needed;
- the canonical in-memory transcript is never mutated;
- reasoning and function-call items remain present;
- exact repository content is always recoverable through the bounded controller tools.

This does not summarize source code semantically or invent state. It removes stale payload bytes while preserving cryptographic identity and exact re-read capability.

## Security and authority

No new credential, shell, network, GitHub, merge, reviewer, Production, trading, or real-capital authority is granted to Codex. Publication remains a separate deterministic controller stage and READY still requires a non-empty candidate plus the exact full QORE Quality Gate.

## Regression evidence

Unit tests pin the real third-run usage regression: raw cumulative usage exceeds 120k while spend-equivalent usage is 48,003 units. Additional adversarial tests verify cache-write/output weighting, invalid usage rejection, deterministic old-output compaction, digest identity, preservation of the two newest outputs, and non-mutation of canonical history.
