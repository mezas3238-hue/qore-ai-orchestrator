from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.openai.com/v1/responses"
EXPECTED = "CODEX_CONNECTION_OK"


def extract_output_text(payload: dict[str, object]) -> str:
    texts: list[str] = []
    output = payload.get("output", [])
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    texts.append(text)
    return "".join(texts).strip()


def main() -> int:
    api_key = os.environ.get("OPENAI_CODEX_API_KEY", "")
    model = os.environ.get("OPENAI_CODEX_MODEL", "gpt-5.3-codex")
    if not api_key:
        print("OPENAI_CODEX_API_KEY is not configured.", file=sys.stderr)
        return 2

    body = {
        "model": model,
        "input": "Return exactly CODEX_CONNECTION_OK and nothing else.",
        "reasoning": {"effort": "low"},
        "max_output_tokens": 64,
        "store": False,
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"OpenAI HTTP error {exc.code}: {detail[:1000]}", file=sys.stderr)
        return 3
    except Exception as exc:  # connection probe should fail closed with concise evidence
        print(f"OpenAI connection error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4

    output_text = extract_output_text(payload)
    usage = payload.get("usage", {})
    safe_summary = {
        "response_id": payload.get("id"),
        "model": payload.get("model"),
        "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
        "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
        "output": output_text,
    }
    print(json.dumps(safe_summary, sort_keys=True))

    if output_text != EXPECTED:
        print("Unexpected Codex response; connection probe failed closed.", file=sys.stderr)
        return 5

    print("Codex API connection verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
