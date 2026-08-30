# PRE-SPEND AUTONOMY RECOVERY — 014

## Purpose

QORE Autonomous V2 may fail closed while reconstructing GitHub state before any GPT-5.6 Sol, Codex, Claude or DeepSeek action has occurred. Such a failure must not strand the autonomous loop when the infrastructure defect is repaired, but it also must not create a hidden retry path, reset budgets, duplicate work, or conceal provider spend.

This contract adds one bounded controller-only recovery path for that exact case.

## Recovery eligibility

A failed Autonomous V2 run is recoverable only when all of the following are proven live from GitHub:

- the run is exactly `QORE Architect autonomous V2` from `.github/workflows/qore-architect-autonomous-v2.yml`;
- it is a completed `workflow_dispatch` run on `main` with conclusion `failure`;
- it was created as the `child_architect_run_id` of one immutable agent-completion resume receipt;
- all state-collection prerequisites completed successfully;
- the exact step `Validate complete snapshot before model spend` failed;
- every model-facing, Sol, Codex and external-reviewer step after that gate is `skipped`;
- the architect artifact contains no `sol-usage`, architect decision, Codex package/dispatch, or reviewer package/dispatch evidence.

Any ambiguity or conflicting evidence fails closed.

## Lineage and budget invariants

Pre-spend recovery is not a new autonomy session and does not consume a new logical cycle. Its receipt copies the previous session identity and preserves exactly:

- `session_id`;
- `cycle_index`;
- cumulative estimated spend;
- Sol calls used;
- Codex jobs used;
- package history;
- all configured cycle, spend, Sol and Codex caps.

The recovery itself records zero model/provider cost and increments only `pre_spend_recovery_count`.

A recovered child becomes the next `child_architect_run_id` in the same receipt lineage, so later agent completions inherit the original budget state rather than starting from zero.

## Anti-loop and anti-duplicate rules

- Maximum pre-spend recoveries per bounded session: **1**.
- A failed child run can dispatch a recovery child at most once.
- Existing dispatched recovery receipts are detected by `recovery_of_child_architect_run_id` before any new dispatch.
- A second recovery attempt returns a non-dispatching stop receipt.
- Recovery never reruns the completed reviewer or agent that caused the preceding wake-up.
- Recovery never deletes or rewrites prior receipts.

## Event paths

Future failures are handled automatically by the existing `QORE agent completion resume` workflow through `workflow_run` completion events for Autonomous V2.

A one-time historical recovery may be activated by an exact main-branch commit that changes only `recovery/architect-pre-spend-current.json`. The request must bind the failed run id, attempt, failed run HEAD, and the exact source resume receipt. This mechanism exists to repair already-terminal pre-spend runs that completed before the automatic trigger was installed.

## Authority boundary

The controller can dispatch only another bounded Autonomous V2 run with the existing execute inputs. It grants no qore-core merge authority, no Production authority, no real-capital authority, and no provider credential expansion.

If any model call, agent dispatch, reviewer package, decision artifact, or other post-gate side effect is observed, this recovery path refuses execution.
