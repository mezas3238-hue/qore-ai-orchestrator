# AUTONOMY-BUDGET-REARM-013

## Purpose

Automatic QORE continuation is intentionally finite. The controller stops when a configured cycle, estimated-spend, Sol-call, or Codex-job cap is reached. A stop is not an error and must never silently reset itself.

This unit provides an explicit human-authorized rearm path. Rearm is a new bounded authorization tranche, not a bypass of the previous budget evidence.

## Eligible source

A rearm request must name the exact completed GitHub Actions run ID of `QORE agent completion resume`. The controller re-fetches that run and requires:

- exact workflow identity;
- completion caused by `workflow_run` or `repository_dispatch` from an agent completion;
- completed status;
- orchestrator `main` execution;
- exact immutable artifact `qore-agent-resume-<run_id>`;
- receipt schema `qore.orchestration.resume.receipt.v1`;
- `dispatched == false` and no child architect run;
- `production_authority == false`;
- valid session, package-history, spend, Sol-call and Codex-job evidence;
- one of the explicitly rearmable stop reasons:
  - `AUTO_RESUME_CYCLE_CAP_REACHED`;
  - `ESTIMATED_SPEND_CAP_REACHED`;
  - `SOL_CALL_CAP_REACHED`;
  - `CODEX_JOB_CAP_REACHED`.

Security, ambiguity, duplicate-event and loop-signature stops are deliberately not eligible for budget rearm.

## Human authorization

`QORE autonomy budget rearm` is `workflow_dispatch` only. The operator must provide the exact stopped resume run ID and explicitly set `confirm_rearm=true`. The workflow may execute only from orchestrator `main`.

Before dispatch, the controller refuses to proceed if Autonomous V2, the Codex worker, or the completion-resume controller already has queued/in-progress work. It also scans immutable prior rearm artifacts and refuses to rearm the same stopped receipt twice.

## New bounded session

A successful rearm dispatches exactly one fresh `QORE Architect autonomous V2` run with the normal bounded execute policy:

- Sol spend confirmed;
- Codex worker execute;
- external reviewer dispatch execute;
- adaptive Sol reasoning.

The next agent completion starts a fresh bounded continuation session with the standard automatic tranche:

- at most 3 automatic resumes;
- at most USD 5.00 controller-estimated OpenAI spend;
- at most 12 Sol-call units;
- at most 3 completed Codex jobs.

The previous session is not erased. The immutable `qore.orchestration.rearm.receipt.v1` records the exact stopped resume run, prior session ID, prior stop reason, prior cumulative spend, prior Sol/Codex counters, prior package history, the new architect seed run, and the new-session policy. Thus a human may authorize another bounded tranche while preserving a complete audit chain.

## Permanent boundaries

Rearm grants only another bounded control-plane cycle. It does not grant Production readiness, provider operational readiness, real-capital authority, productive credentials, deposits, withdrawals, real orders, Risk bypass, or Production activation. It does not modify qore-core or weaken any Quality Gate.
