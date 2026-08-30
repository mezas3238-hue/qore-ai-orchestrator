# SOL incomplete-response hardening — 044

## Scope

Harden the bounded GPT-5.6 Sol architect call without changing role authority, reviewer authority, Production authority, or autonomous cycle caps.

## Problem reproduced

Live architect run `33338976459` passed the canonical snapshot, reviewer-state, Codex-state, protection, model-context, and reasoning-policy gates, then received a Responses API object with `status=incomplete`. The previous runner returned before persisting usage or `incomplete_details`, so the immutable cycle artifact lost the exact terminal reason.

The Responses API defines `max_output_tokens` as the bound covering both reasoning tokens and visible output tokens. A reasoning-heavy response can therefore consume a low bound before emitting the strict JSON decision.

## Contract

1. Every successfully decoded Responses API payload writes a sanitized usage/terminal-state record before decision parsing.
2. The record may contain response/model identity, token counters, response status, the short provider `incomplete_details.reason`, and a short provider error code. It never stores generated text, prompts, credentials, provider error messages, or secrets.
3. `status != completed` remains fail-closed. There is no hidden retry and no inferred decision.
4. Reasoning effort remains controller-selected. Bounded output ceilings become:
   - medium: 6,000
   - high: 8,000
   - xhigh: 16,000
   - max: 20,000
5. The runner hard ceiling remains 20,000. No cycle, Sol-call, spend, Codex-job, reviewer, merge, Production, or real-capital authority is increased.
6. If an incomplete response occurs again, its immutable artifact exposes the sanitized reason and token use so the controller can adjudicate it without a blind retry.

## Non-goals

- No automatic retry inside the Sol runner.
- No `store=true` or remote conversation state.
- No change to qore-core.
- No Production or real-capital authorization.
