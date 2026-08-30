# PUBLIC-QG-LOG-TRANSPORT-018

## Problem

Autonomous V2 run `33330339041` produced a valid DeepSeek Expert `REVIEW_TASK`, but reviewer-package construction could not download the exact qore-core QG job log. The orchestrator-scoped token returned HTTP 403; the reviewer bridge token later returned HTTP 401 on `/actions/jobs/<job_id>/logs` even though metadata reads succeeded.

## Contract

`qore-core` is currently public. GitHub permits public Actions job-log downloads without authentication. QORE therefore keeps authenticated metadata reads through `QORE_CORE_READ_TOKEN`, but transports only the exact `/actions/jobs/<positive-id>/logs` payload without Authorization after independently attesting that the repository is exactly `mezas3238-hue/qore-core`, `private=false`, and `visibility=public`.

If that public attestation fails, package construction fails closed. No fallback exists for arbitrary endpoints or a private qore-core repository.

Both normal Autonomous V2 reviewer packaging and no-model recovery use the same public-log wrapper.

## Recovery retry

A failed reviewer recovery may be retried at most once. A retry is allowed only when every prior matching recovery run is terminal with an explicitly retryable non-success conclusion and its immutable parent artifact contains neither `reviewer-package.json` nor `reviewer-dispatch.json`.

An active or successful prior recovery, any prior package/dispatch side-effect evidence, an unexpected terminal conclusion, ambiguous history, or exhaustion of two total attempts blocks further recovery.

## Authority

This change does not alter qore-core, reviewer semantics, Production authority, real-capital authority, Risk authority, provider credentials, or the budget envelope. It prevents repeated Sol spend for a mechanical transport failure and preserves fail-closed behavior.
