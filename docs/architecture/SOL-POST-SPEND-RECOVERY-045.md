# SOL post-spend recovery — 045

## Purpose

Close the orchestration gap where an Autonomous V2 run has already made one bounded Sol call but fails before producing any architect decision or delegated agent side effect.

This is distinct from pre-spend recovery: the spent call must remain visible in the same session ledger.

## Eligibility

A recovery is permitted only when all of the following are proven from GitHub live state and immutable artifacts:

- exact `QORE Architect autonomous V2` workflow-dispatch run on `main`;
- run is terminal `failure`, attempt 1;
- snapshot, reviewer state, Codex state, model context, and reasoning-policy steps succeeded;
- the initial GPT-5.6 Sol step alone failed;
- every downstream escalation, decision validation, Codex dispatch, and reviewer dispatch step was skipped;
- the architect artifact contains no architect decision, Codex request/dispatch, or reviewer package/dispatch;
- hardened runs contain `sol-usage-initial.json` with `response_status=incomplete` and an allowlisted token-exhaustion reason;
- the single historical run `33338976459` is allowed as a pinned migration exception because it predates hardening 044 and is bound to its exact old orchestrator SHA;
- an exact successful bounded-autonomy rearm receipt proves that the failed run was the seed of the new session;
- no Architect or Codex work is queued/in progress;
- the same failed run/session has not already been recovered.

## Accounting

- the failed Sol attempt counts as exactly one Sol call;
- observed usage is costed with the controller price table when available;
- the pinned legacy incomplete run reserves `UNKNOWN_SOL_PASS_RESERVE_USD` instead of pretending the lost usage was zero;
- before dispatching a replacement Architect, the controller reserves all three possible Sol calls for that child and refuses dispatch if the USD/Sol caps would be exceeded;
- cycle index remains zero until an agent actually completes;
- Codex count and package history remain zero for this new session;
- the child Architect is linked by a normal `qore.orchestration.resume.receipt.v1`, so later callbacks continue through the existing lineage logic.

## Authority

No Production, real-capital, merge, reviewer-semantic, credential, shell, or qore-core authority is added. Recovery can only dispatch the already-governed Autonomous V2 workflow.
