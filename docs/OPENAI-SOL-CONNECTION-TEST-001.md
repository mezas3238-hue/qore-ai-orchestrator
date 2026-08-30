# OPENAI-SOL-CONNECTION-TEST-001

## Purpose

Prove that the private `qore-ai-orchestrator` repository can authenticate to the OpenAI Responses API and invoke `gpt-5.6-sol` with a minimal bounded request.

## Safety boundary

This stage is connectivity-only.

- No `qore-core` checkout.
- No access to `qore-core` contents, issues, pull requests, Actions, or write APIs.
- No GitHub write permission in the workflow.
- No model tools.
- No autonomous loop.
- No branch, commit, PR, issue, or comment publication by the model.
- No Production, trading, broker, credential, or capital authority.
- No API request unless a human manually dispatches the workflow with `confirm_api_spend=true`.
- Exactly one minimal Responses API request per successful dispatch.
- The OpenAI secret is read only from GitHub Actions Secrets and is never printed.

## Model contract

- Provider: OpenAI
- Endpoint: `POST /v1/responses`
- Model: `gpt-5.6-sol`
- Reasoning effort: `none` for this connectivity probe only
- Maximum output: 64 tokens
- Storage: disabled for the probe
- Required output sentinel: `QORE_SOL_CONNECTION_OK`

This probe does not establish Principal Architect capability. That requires a separate staged implementation after connectivity is proven.

## Promotion rule

A successful connection test permits only the next infrastructure stage: read-only QORE reconstruction by Sol under an explicit Principal Architect charter. It does not authorize autonomous engineering, dispatch to other AIs, mutation of `qore-core`, or integration/merge authority.
