# SOL Cost / Context Optimization 004

## Purpose

Reduce OpenAI API token spend without reducing QORE architectural evidence, reviewer independence, or fail-closed behavior.

The full canonical `qore-state.json` remains the immutable cycle evidence. Models receive a deterministic bounded projection produced from that full snapshot.

## Baseline measured from architect cycle 33294341304

- full canonical snapshot: 411,092 characters;
- Sol API input: 100,213 tokens;
- Sol output: 4,964 tokens, including 4,142 reasoning tokens;
- automatic tier selected: `max`;
- cache write: 100,210 tokens.

The first run showed two avoidable costs:

1. full bodies for the entire open-issue backlog and 3,000-character heads for every mission were transported to Sol even when unrelated to the active candidate;
2. a generic `security`/`credential` substring anywhere in the backlog could force `max`, including future or semantic "credential-like" work unrelated to an actual credential/security authority event.

## Bounded model context

`scripts/build_model_context.py` preserves the full snapshot as an artifact and generates `qore-model-context.json`.

Architect context contains the full README, Constitution and canonical roadmap; a bounded mission index and complete architecture path index; complete PR/issue indexes without unrelated bodies; bounded detail for the reviewer-bound or newest candidate and its linked issues; recent main commits/CI; and current Claude/DeepSeek state.

If material omitted evidence is needed, Sol must request evidence rather than infer it.

Hard model-context limits:

- architect context: 180,000 characters;
- Codex engineering context: 70,000 characters.

The representative first-cycle snapshot projects to roughly 116k architect characters and 35k engineer characters while the original 411k full snapshot remains preserved.

## Prompt cache layout

GPT-5.6 Sol receives the stable corpus before live state and uses a stable `prompt_cache_key`. Prompt caching remains implicit with a 30-minute TTL. GitHub, not model state, remains authoritative.

## Adaptive reasoning correction

`auto` now distinguishes active/focused work from historical backlog:

- `medium`: routine reconstruction with no material live work;
- `high`: ordinary live PR/issue/CI coordination;
- `xhigh`: focused architecture/invariant/semantic/governance complexity, material reviewer findings, or several recently active PRs;
- `max`: focused critical security/secret/credential-authority/Production/real-capital/split-brain conditions or later bounded escalation.

Incidental words in unrelated backlog items or phrases such as "credential-like material" do not by themselves force `max`.

Sol can still request one strictly higher bounded retry when the evidence encountered justifies it.

## Non-regression boundaries

This optimization does not alter `qore-core`; move or expose Claude/DeepSeek provider credentials; weaken exact freeze / BASE / HEAD / SYNTHETIC / Quality Gate checks; authorize Production, productive credentials, real capital, or real execution; allow missing evidence to be inferred; or change external reviewer ordering/anti-duplication rules.
