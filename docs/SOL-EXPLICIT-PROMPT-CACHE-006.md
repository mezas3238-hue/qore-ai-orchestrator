# SOL Explicit Prompt Cache 006

## Observation

Architect cycle `33295683756` used the bounded 116,339-character Sol context twice. Both calls reported `cached_tokens=0` and wrote roughly 33.4k input tokens to cache, despite sharing the same stable architectural corpus and cache key.

The model-facing context is intentionally split into a stable corpus (~80.6k characters in that cycle) and dynamic live state (~35.7k characters).

## Correction

GPT-5.6 supports explicit prompt-cache breakpoints. The Sol request now marks the exact end of the stable architectural corpus with:

`prompt_cache_breakpoint: {"mode": "explicit"}`

and changes request cache mode to `explicit` with the existing 30-minute TTL and stable cache key.

This prevents implicit breakpoint selection from rewriting the whole request prefix when only live state or reasoning effort changes. The dynamic state remains outside the breakpoint and therefore cannot contaminate or stale the stable cache boundary.

## Evidence policy

The full canonical snapshot remains the source of truth and immutable cycle evidence. Prompt caching is only a billing/latency optimization; it does not create authority, memory, or state. Any mismatch or cache miss remains safe because the full request is still sent and validated by the API.
