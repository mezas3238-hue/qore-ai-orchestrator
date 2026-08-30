# SOL Autonomous Continuation 007

## Problem observed

Architect cycle `33296517063` correctly selected `xhigh`, used one optimized Sol call, and concluded that PR #466 remained frozen because DeepSeek Expert R94 had failed before model execution. The workflow then published a summary and completed successfully with `NO_ACTION` / `next_actor=NONE`.

That behavior is safe but not the intended operating model. Sol is the resident Principal Architect and must continue coordinating until there is a real external wait or gate. A state synthesis is evidence, not a terminal state.

## Contract change

`NO_ACTION` is removed from the architect decision schema. Sol must classify the next boundary as one of:

- `ENGINEERING_TASK` -> Codex;
- `REVIEW_TASK` -> Claude Code or DeepSeek;
- `WAITING_AGENT` -> exact already-dispatched agent job is observed queued/in-progress;
- `RECONSTRUCTION_REQUIRED` -> internal non-terminal evidence refresh;
- `HUMAN_DECISION_REQUIRED` -> real human authority/gate;
- `PROGRAM_COMPLETE` -> canonical roadmap is actually complete.

A failed reviewer request is not a wait state. The controller must expose reviewer PR/issue/CI state so Sol can see actionable reviewer-infrastructure corrections instead of treating the reviewer as still working.

## Reviewer control-plane visibility

The GitHub bridge token remains isolated from provider API credentials. It additionally needs read access to private reviewer repository Pull Requests and Issues so the orchestrator can collect:

- bounded open PR metadata and bodies;
- bounded open issue metadata and bodies;
- recent Actions runs and exact PR HEAD run status.

Required fine-grained GitHub permissions for the two reviewer repositories:

- Contents: Read and write (existing dispatch contract);
- Actions: Read-only;
- Pull requests: Read-only;
- Issues: Read-only.

If this evidence cannot be collected, the workflow fails before any OpenAI call rather than paying Sol to reason over a known-incomplete control plane.

## Wait proof

`WAITING_AGENT` is independently validated. For Claude/DeepSeek it requires:

1. an exact non-empty package ID;
2. equality with that reviewer's `current_request.package_id`;
3. reviewer control-plane visibility;
4. at least one observed reviewer workflow run in `queued` or `in_progress` state.

A completed/failed historical run cannot satisfy the wait gate.

Codex is currently synchronous PLAN-ONLY, so it cannot yet be represented as a persistent `WAITING_AGENT`; an asynchronous Codex worker will be a later bounded stage.

## Reconstruction continuation

If Sol returns `RECONSTRUCTION_REQUIRED`, the workflow refreshes canonical Core state and private reviewer control-plane state, rebuilds bounded model context, carries the prior non-terminal decision into the refreshed context, and performs one additional bounded Sol step. If Sol still requires reconstruction after that refresh, the controller stops fail-closed via a loop guard. This is a safety stop, not architectural completion.

## Engineering routing

Engineering contracts now identify `target_repository`. Codex PLAN-ONLY may receive bounded engineering context for Core or for reviewer/orchestrator infrastructure. No repository write authority is granted by this change.

## Permanent boundaries

No Core source code is modified by this orchestrator change. No Production, real-capital, productive-credential or real-trading authority is created. Reviewer API keys remain in their existing repositories and are never exposed to Sol or Codex.
