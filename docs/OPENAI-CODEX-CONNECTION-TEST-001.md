# OPENAI-CODEX-CONNECTION-TEST-001

## Purpose

Prove that `qore-ai-orchestrator` can reach the OpenAI Responses API with the dedicated Codex service credential before any engineering or QORE repository access is enabled.

## Model

`gpt-5.3-codex`

## Secret

The workflow consumes `OPENAI_CODEX_API_KEY` only from GitHub Actions Secrets. The secret value must never be committed, printed, copied into issues/PRs, or shared in chat.

## Safety boundary

This probe:

- is manual (`workflow_dispatch`);
- defaults to `confirm_api_spend=false`;
- performs no API request unless the operator explicitly selects `true`;
- performs exactly one minimal Responses API request;
- uses low reasoning effort and a 64-token output cap;
- sets `store=false`;
- has GitHub workflow permission `contents: read` only;
- does not checkout, read, modify, or publish to `qore-core`;
- exposes no coding tools, shell tool, patch tool, GitHub write tool, or autonomous loop;
- grants no Production, execution, trading, or capital authority.

## Acceptance

A successful probe must return exactly `CODEX_CONNECTION_OK` and emit only non-secret response metadata/usage.

Connectivity success proves only API access. It does not prove engineering capability, repository permissions, autonomous operation, QORE integration, or Production readiness.
