# QORE AI Orchestrator — Sol adaptive reasoning and external reviewer routing

## Status

Rollout 002 introduces adaptive GPT-5.6 Sol reasoning and bounded dispatch to the existing Claude Code and DeepSeek reviewer repositories. Rollout 003 adds bounded reviewer-state reconstruction so Sol can observe pending work and consume Claude's completed review artifact on a later cycle.

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
- `xhigh`: cross-cutting architecture, provider-neutrality interactions, compatibility, failover/fencing/reconciliation, state-machine or identity semantics, or material reviewer disagreement/findings.
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

## Reviewer return channel

At the beginning of an architect cycle, when the reviewer bridge credential is configured, the orchestrator reads the existing reviewer repositories before calling Sol.

For Claude it reads the current immutable request and searches for the exact artifact named `claude-<package_id>`. If found, it downloads that artifact through GitHub's signed artifact URL without forwarding the GitHub credential to the signed storage host, extracts only the bounded `claude-review.md`, classifies its mechanical verdict, and attaches the review to the immutable Sol snapshot. Sol must still verify that the request HEAD equals the live qore-core PR HEAD before treating the review as valid evidence.

If the Claude request exists but no artifact exists yet, Sol sees `PENDING_OR_UNKNOWN` and must not duplicate an equivalent review merely because the prior one has not finished.

For DeepSeek the current request is read so Sol can see which Expert/Coder stage is active. Final DeepSeek review evidence continues to come from the exact qore-core pull-request review, where the existing DeepSeek workflow already publishes it.

## Cross-repository GitHub bridge credential

External reviewer observation and real dispatch require one GitHub credential stored only as the orchestrator Actions secret:

```text
QORE_REVIEWER_DISPATCH_TOKEN
```

This is a GitHub repository credential, not an AI-provider key. It should be a fine-grained token restricted to exactly:

```text
mezas3238-hue/qore-claude-reviewer
mezas3238-hue/qore-deepseek-reviewer
```

Required repository permissions:

```text
Contents: Read and write
Actions: Read-only
```

`Contents: Read and write` is needed only to create the new prompt and update `requests/current.json`. `Actions: Read-only` is needed only to observe/download Claude's completed review artifact. No qore-core repository access and no Actions write permission are required.

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
- Real cross-repository writes are allowed only from orchestrator `main`.
- No external reviewer API/OAuth secret is moved to this repository.
- No Production, productive credential, real capital, deposits/withdrawals, or real-money execution authority is granted.
- Any candidate change invalidates prior exact-HEAD external reviews.
- Independent reviewers remain independent; Sol may adjudicate findings but cannot convert its own design into independent certification.
