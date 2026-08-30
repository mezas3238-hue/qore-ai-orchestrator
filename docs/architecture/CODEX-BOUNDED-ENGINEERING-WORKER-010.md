# CODEX BOUNDED ENGINEERING WORKER — 010

## Purpose

Promote Codex from legacy PLAN-ONLY assistance to a bounded real engineering worker without granting the model GitHub credentials, merge authority, reviewer authority, or Production authority.

## Authority separation

`GPT-5.6 Sol` remains Principal Architect and issues an exact engineering contract bound to `source_main_sha`.

`GPT-5.3-Codex` receives only `OPENAI_CODEX_API_KEY` and a local checkout fixed to that exact source SHA. The model has controller-defined local tools only. It never receives `QORE_CODEX_ENGINEER_TOKEN`.

The GitHub write token is exposed only to `publish_codex_candidate_v2.py`, after the worker has returned READY and after a separate controller stage has independently rerun the exact QORE full Quality Gate.

## Worker tools

The worker may:

- list bounded repository files;
- read bounded UTF-8 line ranges;
- search literal text;
- apply bounded unified patches;
- inspect the candidate diff;
- run targeted pytest under `tests/`;
- run the immutable full QORE Quality Gate;
- finish READY or BLOCKED.

It has no arbitrary shell tool, unrestricted command execution, GitHub token, merge function, reviewer credential, Production credential, or provider trading credential.

## Hard bounds

- maximum 16 model turns;
- hard cumulative API token budget: 120,000 tokens per worker invocation;
- maximum patch transport: 120,000 characters;
- maximum 30 changed files;
- path containment under the exact checkout;
- `.git` access forbidden;
- symlink and gitlink/submodule patch modes forbidden;
- READY requires a non-empty candidate plus worker full QG after the final patch;
- budget/turn exhaustion returns BLOCKED and never publishes.

The candidate fingerprint covers both the tracked binary diff and exact SHA-256 digests of every untracked file, so newly created tests/code cannot disappear from the worker evidence fingerprint.

## Independent controller gate

For READY candidates the workflow independently reruns, outside the model tool loop:

1. `ruff check .`
2. `mypy src tests`
3. `pytest --cov=src/qore --cov-report=term-missing`

Only after all three succeed is `qore.codex.controller.qg.v1` created. The publication controller requires that exact artifact and rejects publication if any gate is absent or not SUCCESS.

## Deterministic publication

The publisher:

- verifies exact request/result/controller-QG binding;
- verifies local HEAD is still the source main SHA;
- stages the complete candidate;
- creates a deterministic single-parent commit whose parent is exactly `source_main_sha`;
- uses a deterministic `agent/codex-*` branch;
- never force-pushes an existing branch at a different SHA;
- creates or reuses exactly one Draft PR for that deterministic branch;
- records source SHA, candidate SHA and contract ID;
- declares prior external reviews obsolete for the new candidate.

Codex itself never sees the publication credential.

## Review and Production boundaries

A green Codex candidate is not independently certified. Sol must reconstruct the new candidate, run the normal freeze/reviewer sequence and adjudicate findings.

No worker result, Quality Gate, PR, reviewer result or merge grants Production, real-capital, productive-credential, deposit/withdrawal, or autonomous real-trading authority.
