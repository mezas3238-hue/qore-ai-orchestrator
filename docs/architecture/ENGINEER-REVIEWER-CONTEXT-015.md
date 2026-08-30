# Engineer reviewer context projection — 015

## Incident

Autonomous V2 run `33327311650` reconstructed the complete live QORE and reviewer state successfully, but failed before any GPT-5.6 Sol spend because reviewer evidence copied wholesale into the Codex engineer context expanded it to `84,791` characters, above the hard `70,000` bound.

The architect context itself remained valid at `162,858 / 180,000`. The failure therefore belongs to the Sol→Codex context boundary, not to canonical reconstruction.

## Correction

Sol continues to receive the complete bounded external reviewer state, including exact reviewer-main identity, recent closed correction history and the exact-SHA DeepSeek technical projection.

Codex receives a derived operational projection only:

- reviewer repository/status and current immutable request;
- exact reviewer `main` identity;
- open PR/issue indexes without bodies;
- at most five recent reviewer Actions runs;
- bounded artifact identity and reviewer verdict, never review prose;
- for DeepSeek, exact model/profile/binding/QG transport/stable-fallback summary without implementation file inventories or historical workflow lists.

Historical issue/PR bodies, review text and detailed reviewer implementation inventories remain architect-only evidence.

## Safety properties

- The `70,000` Codex engineer-context ceiling is unchanged and still fails closed.
- No reviewer, provider or Production authority is added to Codex.
- No provider secret is copied into either context.
- No qore-core semantics, Quality Gate, roadmap or acceptance criteria are modified.
- Sol retains the complete evidence needed to adjudicate reviewer infrastructure and routing.
- Codex retains enough live operational identity to avoid duplicating or contradicting active reviewer work.

The exact run-2 artifact projected with this rule yields approximately `43,649 / 70,000` engineer-context characters, leaving about `26,000` characters of headroom without raising the bound.
