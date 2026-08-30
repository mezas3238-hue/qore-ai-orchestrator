# QORE AI Role Rollout 001 — Sol Architect / Codex Engineer

## Purpose

Establish GPT-5.6 Sol as the resident Principal Architect and Codex as the Principal Engineer without placing either model inside QORE Core.

## Separation

- `qore-core` remains the product repository and source of truth.
- `qore-ai-orchestrator` remains external control-plane infrastructure.
- This rollout reads public `qore-core` state and cannot write to it.

## Sol architect behavior

Every architect cycle reconstructs an exact qore-core checkout, reads all files under `docs/roadmap/*.md` and `docs/constitution/*.md`, inspects recent commits, open PRs, open issues and recent main Actions evidence, and produces one strict structured decision bound to the exact main SHA.

There is no human task prompt per cycle. The permanent architect charter plus canonical GitHub state drives the decision. A workflow dispatch is currently only the execution trigger and spend authorization.

Sol must select the smallest safe next action, detect equivalent work, respect roadmap ordering, and fail closed when evidence is incomplete or contradictory.

## Codex engineer behavior

When Sol routes to `CODEX`, the Sol decision must carry an enabled, bounded engineering contract. During Rollout 001 Codex is PLAN-ONLY: it may inspect the supplied repository state and produce an implementation plan, but it cannot mutate qore-core.

Bounded branch/PR write authority is a later gate after this architect-to-engineer chain is validated.

## Independence

Sol is not an independent auditor of its own architecture. Claude Code, DeepSeek, Fable and Opus remain separate roles/repositories as applicable and will be connected by dispatch adapters rather than folded into Core.

## Safety

No Production or real-capital authority is granted. No productive credentials, live orders, deposits/withdrawals, or real-money execution are authorized by this workflow.

## Promotion gate

Promote beyond read-only only after a successful real cycle demonstrates:

1. exact qore-core SHA reconstruction;
2. roadmap and constitution ingestion;
3. strict Sol decision output;
4. correct routing and engineering contract generation;
5. optional Codex plan bound to the same SHA and contract ID;
6. bounded token/usage evidence;
7. no qore-core mutation.
