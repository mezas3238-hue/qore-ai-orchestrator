#!/usr/bin/env python3
"""Minimal, bounded GPT-5.6 Sol connectivity probe.

This script intentionally does not access qore-core, GitHub write APIs, or any
external tool other than the OpenAI Responses API endpoint.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.openai.com/v1/responses"
EXPECTED = "QORE_SOL_CONNECTION_OK"


def main() -> int:
    api_key = os.environ.get("OPENAI_SOL_API_KEY", "")
    if not api_key:
        print("OPENAI_SOL_API_KEY is not configured.", file=sys.stderr)
        return 2

    payload = {
        "model": "gpt-5.6-sol",
        "input": f"Return exactly this text and nothing else: {EXPECTED}",
        "reasoning": {"effort": "none"},
        "max_output_tokens": 64,
        "store": False,
    }

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not print request headers or the secret. HTTP status is enough for
        # the first connectivity gate; detailed provider errors can be examined
        # later through a separately sanitized diagnostic path.
        print(f"OpenAI API request failed with HTTP {exc.code}.", file=sys.stderr)
        return 3
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"OpenAI API connectivity probe failed: {type(exc).__name__}", file=sys.stderr)
        return 4

    output_text: list[str] = []
    for item in body.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                output_text.append(content["text"])

    rendered = "".join(output_text).strip()
    if rendered != EXPECTED:
        print("Model responded, but the bounded sentinel did not match.", file=sys.stderr)
        return 5

    usage = body.get("usage") or {}
    safe_usage = {
        "input_tokens": usage.get("input_tokens"),
        "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }

    print(EXPECTED)
    print("model=gpt-5.6-sol")
    print("usage=" + json.dumps(safe_usage, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
