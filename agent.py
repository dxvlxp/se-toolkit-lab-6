from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


ENV_FILE = Path(".env.agent.secret")
SYSTEM_PROMPT = "You are a concise helpful assistant. Answer the user's question directly."


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def fail(message: str, code: int = 1) -> "NoReturn":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def extract_text(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        fail("LLM response does not contain choices")

    message = choices[0].get("message", {})
    content = message.get("content", "")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()

    return str(content).strip()


def call_llm(question: str) -> str:
    api_key = require_env("LLM_API_KEY")
    api_base = require_env("LLM_API_BASE").rstrip("/")
    model = require_env("LLM_MODEL")

    url = f"{api_base}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=55.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        fail("LLM request timed out")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:500]
        fail(f"LLM API returned HTTP {status}: {body}")
    except httpx.HTTPError as exc:
        fail(f"LLM request failed: {exc}")
    except json.JSONDecodeError:
        fail("LLM API returned invalid JSON")

    answer = extract_text(data)
    if not answer:
        fail("LLM returned an empty answer")

    return answer


def main() -> int:
    load_env_file(ENV_FILE)

    if len(sys.argv) < 2:
        fail('Usage: python agent.py "Your question here"')

    question = sys.argv[1].strip()
    if not question:
        fail("Question must not be empty")

    answer = call_llm(question)
    result = {
        "answer": answer,
        "tool_calls": [],
    }

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
