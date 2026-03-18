from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

import httpx

ENV_FILE = Path(".env.agent.secret")
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_TOOL_CALLS = 10

SYSTEM_PROMPT = """You are a helpful assistant that answers questions using the project wiki.

You have access to two tools:
- list_files(path): List files and directories at a given path
- read_file(path): Read contents of a file

Strategy:
1. Use list_files to discover relevant wiki files in the wiki/ directory
2. Use read_file to read specific files and find the answer
3. Always include the source reference in your answer using format: wiki/filename.md#section-anchor

When answering:
- Be concise and direct
- Always provide a source reference (file path + section anchor like wiki/git-workflow.md#resolving-merge-conflicts)
- If you cannot find the answer, say so clearly
"""

# Tool schemas for OpenAI function calling
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read contents of a file from the project repository",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from project root (e.g., 'wiki/git-workflow.md')",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files and directories at a given path",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path from project root (e.g., 'wiki')",
                    }
                },
                "required": ["path"],
            },
        },
    },
]


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


def fail(message: str, code: int = 1) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"Missing required environment variable: {name}")
    return value


def validate_path(path_str: str) -> Path:
    """Validate and resolve a path to ensure it's within the project directory."""
    # Reject absolute paths
    if os.path.isabs(path_str):
        raise ValueError("Path must be relative")

    # Reject traversal
    if ".." in path_str:
        raise ValueError("Path traversal not allowed")

    # Resolve and verify
    resolved = (PROJECT_ROOT / path_str).resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        raise ValueError("Path outside project directory")

    return resolved


def tool_read_file(path: str) -> str:
    """Read contents of a file from the project repository."""
    try:
        resolved_path = validate_path(path)

        if not resolved_path.exists():
            return f"Error: File not found: {path}"

        if not resolved_path.is_file():
            return f"Error: Not a file: {path}"

        return resolved_path.read_text(encoding="utf-8")

    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error reading file: {e}"


def tool_list_files(path: str) -> str:
    """List files and directories at a given path."""
    try:
        resolved_path = validate_path(path)

        if not resolved_path.exists():
            return f"Error: Path not found: {path}"

        if not resolved_path.is_dir():
            return f"Error: Not a directory: {path}"

        entries = []
        for entry in resolved_path.iterdir():
            suffix = "/" if entry.is_dir() else ""
            entries.append(f"{entry.name}{suffix}")

        return "\n".join(sorted(entries))

    except ValueError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error listing files: {e}"


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool by name with the given arguments."""
    if name == "read_file":
        path = arguments.get("path", "")
        return tool_read_file(path)
    elif name == "list_files":
        path = arguments.get("path", "")
        return tool_list_files(path)
    else:
        return f"Error: Unknown tool: {name}"


def extract_text_from_response(response_json: dict[str, Any]) -> str:
    """Extract text content from LLM response."""
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


def extract_tool_calls_from_response(
    response_json: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract tool calls from LLM response."""
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        return []

    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls")

    if not isinstance(tool_calls, list):
        return []

    result = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue

        function = tc.get("function", {})
        if not isinstance(function, dict):
            continue

        name = function.get("name", "")
        arguments_str = function.get("arguments", "{}")

        try:
            arguments = json.loads(arguments_str)
        except json.JSONDecodeError:
            arguments = {}

        result.append({"id": tc.get("id", ""), "name": name, "arguments": arguments})

    return result


def call_llm(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Call the LLM API with messages and optional tools."""
    api_key = require_env("LLM_API_KEY")
    api_base = require_env("LLM_API_BASE").rstrip("/")
    model = require_env("LLM_MODEL")

    url = f"{api_base}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }

    if tools:
        payload["tools"] = tools

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

    return data


def extract_source_from_answer(answer: str) -> str:
    """Extract source reference from the answer text."""
    # Look for patterns like wiki/filename.md#section-anchor
    pattern = r"(wiki/[\w-]+\.md#[\w-]+)"
    match = re.search(pattern, answer)
    if match:
        return match.group(1)

    # Look for just file reference
    pattern_file = r"(wiki/[\w-]+\.md)"
    match = re.search(pattern_file, answer)
    if match:
        return match.group(1)

    return ""


def run_agent_loop(question: str) -> dict[str, Any]:
    """Run the agentic loop: LLM → tool calls → execute → back to LLM."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    tool_calls_log: list[dict[str, Any]] = []

    for iteration in range(MAX_TOOL_CALLS):
        # Call LLM with tool schemas
        response = call_llm(messages, tools=TOOL_SCHEMAS)

        # Extract tool calls
        tool_calls = extract_tool_calls_from_response(response)

        if tool_calls:
            # Execute each tool call
            for tc in tool_calls:
                result = execute_tool(tc["name"], tc["arguments"])

                tool_calls_log.append(
                    {"tool": tc["name"], "args": tc["arguments"], "result": result}
                )

                # Append tool result to messages as a tool response
                messages.append(
                    {"role": "tool", "tool_call_id": tc["id"], "content": result}
                )

            # Continue loop to let LLM process results
            continue
        else:
            # No tool calls - extract final answer
            answer = extract_text_from_response(response)
            source = extract_source_from_answer(answer)

            return {"answer": answer, "source": source, "tool_calls": tool_calls_log}

    # Max iterations reached
    return {
        "answer": "Reached maximum tool calls limit.",
        "source": "",
        "tool_calls": tool_calls_log,
    }


def main() -> int:
    load_env_file(ENV_FILE)

    if len(sys.argv) < 2:
        fail('Usage: python agent.py "Your question here"')

    question = sys.argv[1].strip()
    if not question:
        fail("Question must not be empty")

    result = run_agent_loop(question)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
