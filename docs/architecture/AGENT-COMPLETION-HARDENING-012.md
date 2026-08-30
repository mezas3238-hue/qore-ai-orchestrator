# AGENT-COMPLETION-HARDENING-012

## Scope

This unit hardens the event-driven completion gate that resumes GPT-5.6 Sol after bounded Codex, Claude, or DeepSeek work. It does not modify qore-core and grants no Production or real-capital authority.

## Exact reviewer identity

Cross-repository callbacks are lookup hints, never semantic PASS evidence. The orchestrator independently re-fetches the exact reviewer workflow run and requires all of the following before a fresh Sol cycle may be dispatched:

- allowlisted reviewer repository and actor;
- exact completed run ID and run attempt;
- exact canonical workflow name;
- `workflow_dispatch` origin;
- reviewer `main` head branch;
- valid exact reviewer HEAD SHA;
- `display_title` bound to the actor-specific autonomous package ID;
- exact package agreement between callback, run title, and `requests/current.json` at the run HEAD;
- exact parent Autonomous V2 package binding from the immutable architect artifact.

GitHub Contents API Base64 is whitespace-normalized and then decoded with strict Base64 validation. Malformed content fails closed.

## Explicit continuation budgets

The immutable resume receipt now carries four independent anti-loop/budget dimensions:

- maximum automatic resume cycles: 3;
- maximum controller-estimated OpenAI spend: USD 5.00;
- maximum Sol-call units: 12;
- maximum completed Codex jobs: 3.

Autonomous V2 can issue at most three bounded Sol calls in one architect run (initial, optional escalation, optional reconstruction). The completion gate therefore reserves three Sol-call units before authorizing another architect run. If the remaining Sol-call budget cannot cover a complete bounded architect run, continuation stops before dispatch.

A completed Codex worker increments the cumulative Codex-job count. Reaching the configured Codex-job cap stops before another paid Sol run. This is intentionally conservative: budget safety outranks speculative continuation.

Historical lineage receipts that predate explicit Sol/Codex counters cannot silently continue a new bounded session. A lineage receipt missing these counters fails closed and requires an explicit rearm/migration path rather than inferred zero usage.

## Semantics

A GitHub workflow conclusion is mechanical evidence only. Neither `success` nor a callback constitutes reviewer approval. Sol must reconstruct GitHub state and independently adjudicate reviewer evidence on the next cycle.

`WAITING_AGENT` remains legitimate only when the exact delegated job is actually queued or in progress. A completion callback exists solely to wake the controller after that wait boundary.

## Permanent boundaries

- No provider dependency is introduced into qore-core.
- No reviewer receives qore-core write authority from this change.
- No Production account, real capital, productive credential, real order, deposit, withdrawal, Risk bypass, or Production activation is authorized.
- Program-D semantic completeness remains distinct from provider readiness, operational readiness, Production readiness, and real-capital authorization.
