# QORE AI Orchestrator — Sol adaptive reasoning and external reviewer routing

## Status

Rollout 002 introduces adaptive GPT-5.6 Sol reasoning and bounded dispatch to the existing Claude Code and DeepSeek reviewer repositories.

This repository remains an external control plane. It is not part of `qore-core`, and no AI model becomes part of Core.

## Agent topology

```text
qore-ai-orchestrator
  GPT-5.6 Sol — Principal Architect / coordinator
  GPT-5.3-Codex — Principal Engineer (current rollout: PLAN-ONLY)
        |
        +--> qore-claude-reviewer   — existing Claude Code reviewer
        +--> qore-deepseek-reviewer — existing DeepSeek reviewer

qore-core — separate canonical product repository / sole source of truth
```

Claude and DeepSeek stay in their current repositories. Their provider credentials are not copied into the orchestrator.

## Adaptive Sol reasoning

Default mode is `auto`. The deterministic controller selects an initial effort before the Sol request:

- `medium`: routine reconstruction, clear status classification, and low-risk unambiguous coordination.
- `high`: normal material technical coordination, active PR analysis, ordinary CI anomalies, or review routing.
- `xhigh`: cross-cutting architecture, provider-neutrality interactions, compatibility, failover/fencing/reconciliation, state-machine or identity semantics, or material reviewer disagreement.
- `max`: critical security/governance, invariant contradiction, Production/real-capital/credential authority, split-brain/safety authority ambiguity, serious architecture contradiction, or critical human-gate recommendation.

Sol may request one strictly higher effort after its initial pass when the evidence encountered justifies it. The orchestrator permits at most one escalated retry on the same immutable snapshot. It never loops reasoning tiers without bound.

The workflow also allows an explicit `medium`, `high`, `xhigh`, or `max` override for controlled diagnostics. `auto` is the normal operating mode.

## Reviewer routing

Sol may choose `CLAUDE_CODE` or `DEEPSEEK` only for an existing open qore-core PR and must emit a structured review contract. Sol does not invent the exact freeze or Quality Gate identifiers.

Before any external dispatch, deterministic code reconstructs live:

```text
PR open/unmerged
BASE
HEAD
SYNTHETIC
synthetic parents == BASE HEAD
synthetic tree == HEAD tree
exact successful pull_request QORE CI run for HEAD
exact successful quality job
executed synthetic checkout
Ruff
Mypy
pytest
TOTAL coverage
```

If any binding fails, no external reviewer request is written.

For DeepSeek, `DEEPSEEK_CODER` is additionally blocked unless exact-HEAD DeepSeek Expert evidence already exists in the canonical qore-core PR evidence supplied to Sol.

## Existing reviewer repositories remain authoritative for execution

The orchestrator writes only a new immutable prompt path and a replacement `requests/current.json` into the selected existing reviewer repository. The existing push-triggered `*-auto-dispatch.yml` then performs the actual review using the credentials already stored there.

Therefore the orchestrator does not require and must not receive:

```text
CLAUDE_CODE_OAUTH_TOKEN
DEEPSEEK_API_KEY
```

Those credentials remain isolated inside their current reviewer repositories.

## Cross-repository GitHub dispatch credential

Real external dispatch (`external_dispatch_mode=execute`) requires one GitHub credential stored only as the orchestrator Actions secret:

```text
QORE_REVIEWER_DISPATCH_TOKEN
```

This is a GitHub repository-write credential, not an AI-provider key. It should be a fine-grained token restricted to exactly:

```text
mezas3238-hue/qore-claude-reviewer
mezas3238-hue/qore-deepseek-reviewer
```

Required repository permission: `Contents: Read and write`. No qore-core repository access and no Actions write permission are required by this bridge.

The token value must never be committed, printed, placed in Variables, or sent through model prompts.

## Dispatch modes

The architect workflow exposes:

- `off`: do not build or dispatch an external reviewer package.
- `dry_run` (default): build and validate the exact package as an artifact but do not write to reviewer repositories.
- `execute`: after all deterministic gates pass, write the package to the selected existing reviewer repository.

The first integrated run of this rollout should use `dry_run` before `execute` is enabled.

## Permanent safety boundaries

- qore-core stays separate.
- The architect cycle itself has `contents: read` repository permission.
- No external reviewer API/OAuth secret is moved to this repository.
- No Production, productive credential, real capital, deposits/withdrawals, or real-money execution authority is granted.
- Any candidate change invalidates prior exact-HEAD external reviews.
- Independent reviewers remain independent; Sol may adjudicate findings but cannot convert its own design into independent certification.
