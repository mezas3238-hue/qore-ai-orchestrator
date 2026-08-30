# Agent Completion Resume 011

## Objective

Close the asynchronous gap after an agent finishes without replacing Autonomous V2 or moving provider credentials into the orchestrator.

The controller remains event-driven:

1. Sol issues exactly one bounded agent package.
2. The architect cycle stops only at an observed `WAITING_AGENT` boundary.
3. Completion of the exact agent run wakes a deterministic resume gate.
4. The gate verifies the run/package/parent-architect binding and budget before any new paid call.
5. If authorized, it dispatches exactly one fresh `QORE Architect autonomous V2` cycle.
6. Sol reconstructs GitHub live and semantically adjudicates the result. GitHub `success` is never treated as semantic PASS.

## Trigger selection

### Codex — same repository

`workflow_run` is the primary completion event for `QORE Codex engineer worker` because the worker and controller live in `qore-ai-orchestrator`.

The resume controller re-fetches the workflow run from GitHub, requires the trusted workflow identity and `workflow_dispatch` origin, extracts the deterministic package from the run title, downloads the exact worker artifact, and binds it to the `architect_run_id` carried in `codex-request.json`.

### Claude / DeepSeek — cross repository

`repository_dispatch` with event type `qore_agent_completion_v1` is the cross-repository callback contract. The callback payload is only a lookup hint; it is not trusted as review evidence.

The orchestrator uses its existing reviewer bridge read authority to re-fetch the reviewer run and `requests/current.json` at the exact reviewer run HEAD, then binds the package back to the immutable `reviewer-package.json` produced by the parent architect run.

Reviewer provider API keys remain in the reviewer repositories. A separate narrow callback credential will be required in the reviewer repositories to create the `repository_dispatch` event against `qore-ai-orchestrator`; it must not contain provider credentials.

## Why not a workflow_run chain for everything

`workflow_run` is repository-local and GitHub limits chains of `workflow_run`-triggered workflows. The resume gate therefore uses `workflow_run` only to observe same-repository Codex completion, then uses `workflow_dispatch` to start the next Autonomous V2 cycle. `workflow_dispatch` is an explicit GitHub exception that can create a new workflow run when initiated with the repository `GITHUB_TOKEN`.

Cross-repository completion uses an authenticated `repository_dispatch` callback. A scheduled watchdog may be added later only as recovery for a lost callback, never as the primary event transport.

## Exact binding

A Codex completion must bind all of:

- actor = `CODEX`;
- orchestrator repository;
- workflow run ID and run attempt;
- trusted worker workflow and `workflow_dispatch` origin;
- worker run HEAD on orchestrator `main`;
- deterministic `QORE-CODEX-*` package ID;
- worker `codex-request.json` package ID;
- request `architect_run_id`;
- request `source_main_sha`;
- parent architect `codex-engineering-request.json` package and source SHA.

A reviewer completion must bind all of:

- allowlisted reviewer repository and actor;
- workflow run ID and run attempt;
- exact reviewer run HEAD;
- `requests/current.json` from that run HEAD;
- exact orchestrator package ID;
- parent architect run ID encoded by the package;
- parent architect `reviewer-package.json` repository/package/candidate HEAD.

The run conclusion is evidence, not semantic adjudication. Sol always re-observes GitHub after resume.

## Budget and loop guard

Automatic continuation is initially deliberately bounded:

- maximum automatic resumes per lineage: **3**;
- maximum controller-estimated cumulative OpenAI spend before a new resume: **USD 5.00**;
- completion event identity: `actor:repository:workflow_run_id:run_attempt`;
- exact duplicate completion events are not redispatched;
- repeating an already-seen agent package in the same lineage is a loop signature and stops fail-closed;
- multiple receipts claiming the same child architect run are an ambiguous lineage and fail closed.

The session ID is derived from the first parent architect run: `QORE-ORCH-R<run_id>`. No mutable main-branch state file is required. Each completion gate uploads an immutable resume receipt that records the lineage, cumulative estimated spend, package history and exact child architect run ID.

The next completion discovers its parent receipt by the exact child architect run ID. This preserves lineage across independent workflow runs without changing the existing Codex request schema.

## Cost accounting

Controller pricing is pinned in reviewed code rather than fetched dynamically during an autonomous run.

As of 2026-08-30 the regular text-token prices used by the guard are:

- GPT-5.6 Sol: input $4.00 / MTok, cached input $0.40 / MTok, output $20.00 / MTok; explicit cache writes are accounted at 1.25x input price.
- GPT-5.3-Codex: input $1.75 / MTok, cached input $0.175 / MTok, output $14.00 / MTok; explicit cache writes are accounted at 1.25x input price.

Observed usage artifacts are priced from uncached, cached, cache-write and output tokens. If Codex has no usage artifact after a failed completion, the gate reserves USD 1.90 rather than assuming zero spend. If the current Autonomous V2 reconstruction path may have overwritten one intermediate Sol usage file, the gate reserves USD 1.25 for that possible paid pass.

Changing model prices or spend limits is a reviewed controller change. The gate never lowers Quality Gates to save budget.

## Authority separation

- Sol decides and adjudicates.
- Codex implements and prepares candidates.
- Claude and DeepSeek review independently.
- The deterministic completion controller validates identity, lineage, spend and dispatch conditions.
- GitHub is the canonical event/state bus.

The completion controller does not merge, certify semantic PASS, authorize Production, expose provider credentials, or perform real-money trading.

## Rollout

1. Merge the orchestrator completion receiver and regressions.
2. Run its manual dry-run diagnostic; this spends no model API budget.
3. Run Autonomous V2 with a deliberately small first operational session.
4. Verify a real Codex completion automatically creates one resume receipt and one child Autonomous V2 run.
5. Add the narrow cross-repository callback workflows to Claude and DeepSeek reviewer repositories after the callback credential is configured.
6. Add a low-frequency watchdog only as callback-loss recovery.
