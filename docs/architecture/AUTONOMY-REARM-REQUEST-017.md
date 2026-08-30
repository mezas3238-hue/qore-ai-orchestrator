# AUTONOMY-REARM-REQUEST-017

## Purpose

Provide an auditable fallback for explicit bounded-autonomy rearm when the connected GitHub control surface cannot invoke `workflow_dispatch` directly.

This is not an automatic budget bypass. It is a second activation channel for the existing `QORE autonomy budget rearm` controller and preserves the same stopped-receipt validation, eligible stop-reason allowlist, active-work guard, previous-rearm deduplication, new-session caps, immutable receipt, and Production prohibition.

## Activation contract

The normal manual `workflow_dispatch` path remains unchanged. The additional path is a push to `main` that changes exactly one file:

`recovery/rearm/autonomy-rearm-current.json`

The request must contain exactly:

- `schema_version = qore.autonomy.rearm.request.v1`;
- positive `stopped_resume_run_id`;
- `confirmation = REARM_BOUNDED_AUTONOMY`;
- a non-empty bounded reason;
- `production_authority = false`.

The controller re-fetches the exact `GITHUB_SHA` commit from GitHub and refuses activation unless its file list contains exactly that request path and nothing else. It then revalidates the exact stopped resume run and immutable receipt before dispatching anything.

## Same anti-bypass gates

Both activation channels execute within the same workflow history and use the same artifact name `qore-autonomy-rearm-<run_id>`. Therefore `previous_rearms()` sees either channel and prevents the same stopped receipt from being rearmed twice.

Only these existing stop reasons remain eligible:

- `AUTO_RESUME_CYCLE_CAP_REACHED`;
- `ESTIMATED_SPEND_CAP_REACHED`;
- `SOL_CALL_CAP_REACHED`;
- `CODEX_JOB_CAP_REACHED`.

Security, ambiguity, duplicate, or loop-signature stops remain non-rearmable.

Before dispatch, the controller still requires no active Autonomous V2, Codex worker, or completion-resume work. A successful rearm starts a new bounded session with the existing default caps; it does not reset or rewrite prior audit evidence.

## Authority

The request path grants no Production, trading, real-capital, merge, reviewer, shell, or credential authority. The request is authorization only for one new bounded Autonomous V2 session after an already-eligible budget/cycle stop.
