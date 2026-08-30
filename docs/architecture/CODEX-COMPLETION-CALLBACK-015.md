# CODEX COMPLETION CALLBACK — 015

## Purpose

A bounded Codex worker can be started by `QORE Architect autonomous V2` through GitHub's `workflow_dispatch` API. GitHub does not reliably create a downstream `workflow_run` continuation when the source workflow itself was created by the repository `GITHUB_TOKEN`. Therefore Codex completion cannot depend exclusively on implicit workflow chaining.

This contract makes Codex completion an explicit, authenticated event, matching the existing Claude/DeepSeek completion bus.

## Normal completion path

The Codex workflow remains split by authority:

1. `worker` performs the bounded GPT-5.3-Codex task, controller QG when READY, optional isolated qore-core candidate publication, summary, and immutable artifact upload.
2. `completion-callback` runs only after the `worker` job completed successfully and only for a spend-enabled worker run.
3. The callback job has no model credentials and receives only orchestrator-repository `contents:write` plus `actions:read` authority.
4. It emits `repository_dispatch` type `qore_agent_completion_v1` with only:
   - schema version;
   - orchestrator repository identity;
   - actor `CODEX`;
   - workflow run id;
   - run attempt;
   - exact package id.

No Codex result semantics are placed in the callback payload.

## Receiver authentication

The completion receiver independently re-fetches GitHub and requires:

- repository exactly `mezas3238-hue/qore-ai-orchestrator`;
- actor exactly `CODEX`;
- package format `QORE-CODEX-<main12>-<16hex>`;
- exact run id and attempt;
- exact workflow path `.github/workflows/codex-engineer-worker.yml`;
- event `workflow_dispatch` on `main`;
- valid orchestrator HEAD;
- exact package-bound display title;
- source run either still `in_progress` with no conclusion, or already `completed/success`;
- exactly one `worker` job already `completed/success`;
- exact non-expired artifact `qore-codex-worker-<run_id>`;
- immutable request schema/package/source-main/parent-architect binding;
- worker-result schema/source-main binding and `production_authority=false`;
- observed usage cost when available, otherwise the existing conservative reserve.

The source run may still be `in_progress` because the callback itself is the final job. The completed `worker` job plus already-finalized artifact form the exact completion boundary.

## Deduplication

The explicit repository callback and legacy `workflow_run` fallback deliberately produce the same event key:

`CODEX:<orchestrator-repo>:<run-id>:<attempt>`

The existing immutable receipt ledger therefore permits at most one continuation dispatch even if GitHub happens to deliver both mechanisms.

## Historical replay

`QORE Codex completion replay` exists only to recover a terminal Codex completion that predated this explicit callback path. A replay is activated by a `main` commit changing only `recovery/codex-completion-current.json`.

Before emitting the same repository event it re-fetches and binds:

- terminal successful Codex workflow run;
- exact attempt, main HEAD, workflow path and package title;
- exact successful `worker` job;
- exact non-expired worker artifact.

The normal receiver then performs the full independent validation again. Replay does not call Codex and does not create new engineering semantics.

## Authority boundary

This callback grants no qore-core write authority, reviewer authority, merge authority, Production authority, trading authority or real-capital authority. The callback is a wake-up signal only; Sol remains responsible for adjudicating the exact worker result from GitHub evidence.
