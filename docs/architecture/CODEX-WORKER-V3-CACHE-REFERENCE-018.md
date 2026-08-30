# CODEX WORKER V3 — CACHE-STABLE STATELESS LOOP + BOUNDED HISTORICAL REFERENCE

## Problem closed

Two independent defects were reproduced from real Codex runs on 2026-08-30.

1. V2 applied rolling compaction to old tool outputs. Because the stateless Responses API conversation is resent every turn, changing an already-sent item destroys the common prompt prefix and sharply reduces prompt-cache reuse. A pre-compaction run observed 91,520 cached input tokens out of 124,147 input tokens; the rolling-compaction run observed only 5,760 cached tokens out of 119,780 input tokens and hit the 120,000 input-price-equivalent budget.
2. The architect contract required Codex to preserve work from exact historical qore-core HEAD `df934e5585f59dd0aef17f9ece108d6f39204470`, but V2 exposed no bounded tool capable of reading an historical commit or its diff. The instruction was therefore not executable through the worker capability surface.

## V3 contract

`run_codex_engineer_worker_v3.py` keeps all V2 authority boundaries and hard limits:

- model remains `gpt-5.3-codex`;
- maximum turns remain 16;
- spend-equivalent stop threshold remains 120,000 units;
- `store` remains `false`;
- no `previous_response_id` server-side conversation state is introduced;
- no GitHub credential, arbitrary shell, network tool, merge/review authority, Production authority, real-capital authority, or provider authority is exposed to Codex;
- candidate publication still occurs only outside the model worker after the independent controller Quality Gate.

### Cache-stable stateless transcript

The model-facing history is copied without retroactively changing prior items. New outputs are appended only. This preserves the common prefix required for prompt-cache reuse while retaining stateless/ZDR-compatible replay semantics.

V3 intentionally does **not** use `previous_response_id`; the privacy/state boundary is unchanged.

### Historical reference diff

V3 adds one read-only local tool, `reference_diff`.

The allowlist is derived deterministically from exact 40-hex SHA values literally present in the immutable architect engineering contract. `source_main_sha` is excluded. At most eight historical references are accepted.

The tool:

- accepts only an allowlisted exact SHA;
- proves the commit object exists in the already-cloned qore-core repository;
- runs only local read-only `git diff` operations from exact `source_main_sha` to that reference;
- returns bounded changed-file names and bounded diff text;
- cannot fetch, write, checkout, merge, commit, push, access `.git` through a model path, or broaden to an unmentioned SHA.

### Safe per-turn telemetry

The usage artifact now records `turn_trace` entries containing only:

- turn number;
- tool name;
- API token counters;
- cumulative spend-equivalent units;
- rendered tool-output character count.

Arguments, patch text, file contents, secrets, and tool outputs are not copied into telemetry.

## Acceptance

V3 is acceptable only if repository regressions prove:

- V3 does not increase the 16-turn or 120k spend-equivalent limits;
- old conversation items remain identical after later turns are appended;
- historical SHA extraction is exact, deterministic, deduplicated, bounded, and excludes source main;
- `reference_diff` rejects any unallowlisted SHA and performs no working-tree mutation;
- telemetry carries no arguments or content;
- the live Codex workflow invokes V3, not V2;
- the existing static suite remains green;
- a real Codex run demonstrates materially restored cache reuse and can consume an explicitly contracted historical reference without capability mismatch.

No statement in this document grants Production readiness, operational readiness, real-capital authority, or trading authority.
