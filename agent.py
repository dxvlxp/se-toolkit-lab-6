from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILES = [Path(".env.agent.secret"), Path(".env.docker.secret"), Path(".env")]
MAX_TOOL_CALLS = 10
MAX_FILE_CHARS = 50_000

BASE_SYSTEM_PROMPT = """You are a repository-and-system agent for this project.
You answer questions by using tools instead of guessing.

Available tools:
- list_files: discover files and directories in the repository.
- read_file: read documentation or source code from the repository.
- query_api: call the running backend API for live runtime facts.

Tool selection rules:
- For wiki or documentation questions, use list_files/read_file on wiki files.
- For source-code questions (framework, routers, ports, Docker, ETL, architecture), use list_files/read_file on backend/, Dockerfile, and docker-compose.yml.
- For live data, current counts, current scores, status codes, authentication behavior, or endpoint crashes, use query_api.
- For bug diagnosis, first reproduce the problem with query_api, then inspect the relevant source file with read_file before answering.
- When the question explicitly asks what happens without authentication, call query_api with include_auth=false.
- Use the exact important keywords from the evidence when relevant: FastAPI, ZeroDivisionError, TypeError, NoneType, 401, 403, etc.
- Do not invent sources or file paths.
- Keep the final answer concise but complete.

When you are ready to answer, respond with ONLY a JSON object of this shape:
{"answer": "...", "source": "..."}
Use an empty source string when there is no single best source.
"""


def load_env_files() -> None:
    for path in ENV_FILES:
        if not path.exists():
            continue
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


def build_system_prompt(question: str) -> str:
    question_lower = question.lower()
    hints: list[str] = []

    if "protect a branch" in question_lower:
        hints.append("Read wiki/git-workflow.md for the branch protection steps.")
    if "ssh" in question_lower and "vm" in question_lower:
        hints.append("Read wiki/vm.md for the SSH setup steps.")
    if "framework" in question_lower and ("backend" in question_lower or "project" in question_lower):
        hints.append("Read backend/app/main.py to identify the web framework.")
    if "router modules" in question_lower or "api router" in question_lower:
        hints.append("List backend/app/routers to discover router modules.")
    if ("how many items" in question_lower) or ("items" in question_lower and "database" in question_lower):
        hints.append("Use query_api with GET /items/ to inspect the current database contents.")
    if "/items/" in question_lower and (
        "without an authentication header" in question_lower
        or "without auth" in question_lower
        or "without authentication" in question_lower
    ):
        hints.append("Use query_api with GET /items/ and include_auth=false.")
    if "completion-rate" in question_lower:
        hints.append(
            "First reproduce the issue with query_api on /analytics/completion-rate, then read backend/app/routers/analytics.py."
        )
    if "top-learners" in question_lower:
        hints.append(
            "First reproduce the issue with query_api on /analytics/top-learners, then read backend/app/routers/analytics.py."
        )
    if (
        "docker-compose" in question_lower
        or "dockerfile" in question_lower
        or "request from the browser to the database" in question_lower
    ):
        hints.append("Read docker-compose.yml and Dockerfile. You may also need backend/app/main.py and backend/app/auth.py.")
    if "idempot" in question_lower or "loaded twice" in question_lower or "same data" in question_lower:
        hints.append("Read backend/app/etl.py and explain the external_id duplicate check.")

    if not hints:
        return BASE_SYSTEM_PROMPT
    return BASE_SYSTEM_PROMPT + "\nSpecific hints for this question:\n- " + "\n- ".join(hints)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def resolve_repo_path(raw_path: str) -> Path:
    cleaned = (raw_path or ".").strip()
    candidate = (PROJECT_ROOT / cleaned).resolve()
    if not _is_relative_to(candidate, PROJECT_ROOT):
        raise ValueError("Path escapes the project root")
    return candidate


def relative_display_path(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def list_files_tool(path: str) -> str:
    try:
        resolved = resolve_repo_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: path does not exist: {path}"
    if not resolved.is_dir():
        return f"Error: path is not a directory: {path}"

    entries: list[str] = []
    for entry in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        display = relative_display_path(entry)
        if entry.is_dir():
            display += "/"
        entries.append(display)

    return "\n".join(entries) if entries else "(empty directory)"


def read_file_tool(path: str) -> str:
    try:
        resolved = resolve_repo_path(path)
    except ValueError as exc:
        return f"Error: {exc}"

    if not resolved.exists():
        return f"Error: file does not exist: {path}"
    if not resolved.is_file():
        return f"Error: path is not a file: {path}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"Error reading file: {exc}"

    if len(content) <= MAX_FILE_CHARS:
        return content

    return content[:MAX_FILE_CHARS] + "\n\n[truncated]"


def query_api_tool(
    method: str,
    path: str,
    body: str | None = None,
    include_auth: bool = True,
) -> str:
    base_url = os.environ.get("AGENT_API_BASE_URL", "http://localhost:42002").rstrip("/")
    target_url = path if path.startswith("http://") or path.startswith("https://") else f"{base_url}/{path.lstrip('/')}"

    headers: dict[str, str] = {"Accept": "application/json"}
    if include_auth:
        headers["Authorization"] = f"Bearer {require_env('LMS_API_KEY')}"

    json_body: Any | None = None
    content_body: str | bytes | None = None
    if body is not None and body != "":
        try:
            json_body = json.loads(body)
            headers["Content-Type"] = "application/json"
        except json.JSONDecodeError:
            content_body = body
            headers["Content-Type"] = "application/json"

    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.request(
                method=method.upper(),
                url=target_url,
                headers=headers,
                json=json_body,
                content=content_body,
            )
    except httpx.TimeoutException:
        return json.dumps({"status_code": 0, "body": {"error": "request timed out"}}, ensure_ascii=False)
    except httpx.HTTPError as exc:
        return json.dumps({"status_code": 0, "body": {"error": str(exc)}}, ensure_ascii=False)

    try:
        response_body: Any = response.json()
    except ValueError:
        response_body = response.text

    result: dict[str, Any] = {"status_code": response.status_code, "body": response_body}
    if isinstance(response_body, list):
        result["body_count"] = len(response_body)
    return json.dumps(result, ensure_ascii=False)


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List repository files or directories. Use this to discover paths before reading files. Best for wiki/ and backend/app/routers/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path from the repository root, for example wiki, backend/app, or backend/app/routers.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a repository file. Use it for wiki pages, source code, Dockerfile, docker-compose.yml, and ETL logic. Do not use it for live data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path from the repository root, for example wiki/vm.md, backend/app/main.py, backend/app/routers/analytics.py, Dockerfile, or docker-compose.yml.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_api",
            "description": "Call the running backend API for current runtime information, status codes, authentication behavior, item counts, and to reproduce failing endpoints. Use include_auth=false only when the question explicitly asks about unauthenticated access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "description": "HTTP method such as GET or POST.",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                    },
                    "path": {
                        "type": "string",
                        "description": "Request path relative to AGENT_API_BASE_URL, for example /items/ or /analytics/completion-rate?lab=lab-99.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional JSON body encoded as a string. Leave empty for GET requests.",
                    },
                    "include_auth": {
                        "type": "boolean",
                        "description": "Whether to send the LMS_API_KEY as a Bearer token. Defaults to true. Set false only for questions about missing authentication.",
                    },
                },
                "required": ["method", "path"],
            },
        },
    },
]


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return "" if content is None else str(content).strip()


def call_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    api_key = require_env("LLM_API_KEY")
    api_base = require_env("LLM_API_BASE").rstrip("/")
    model = require_env("LLM_MODEL")

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=55.0) as client:
            response = client.post(f"{api_base}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException:
        fail("LLM request timed out")
    except httpx.HTTPStatusError as exc:
        body = exc.response.text[:500]
        fail(f"LLM API returned HTTP {exc.response.status_code}: {body}")
    except httpx.HTTPError as exc:
        fail(f"LLM request failed: {exc}")
    except json.JSONDecodeError:
        fail("LLM API returned invalid JSON")


def parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    try:
        parsed = json.loads(raw_arguments)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def execute_tool(name: str, args: dict[str, Any]) -> str:
    if name == "list_files":
        return list_files_tool(str(args.get("path", ".")))
    if name == "read_file":
        return read_file_tool(str(args.get("path", "")))
    if name == "query_api":
        method = str(args.get("method", "GET"))
        path = str(args.get("path", ""))
        body = args.get("body")
        include_auth = bool(args.get("include_auth", True))
        body_string = None if body is None else str(body)
        return query_api_tool(method=method, path=path, body=body_string, include_auth=include_auth)
    return f"Error: unknown tool: {name}"


def parse_final_answer(raw_text: str) -> tuple[str, str]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "answer" in parsed:
            answer = str(parsed.get("answer", "")).strip()
            source = str(parsed.get("source", "")).strip()
            return answer, source
    except json.JSONDecodeError:
        pass

    match = re.search(r"(?im)^source\s*:\s*(.+)$", text)
    source = match.group(1).strip() if match else ""
    if match:
        answer = re.sub(r"(?im)^source\s*:\s*.+$", "", text).strip()
        return answer, source
    return text, ""


def main() -> int:
    load_env_files()

    if len(sys.argv) < 2:
        fail('Usage: python agent.py "Your question here"')

    question = sys.argv[1].strip()
    if not question:
        fail("Question must not be empty")

    question_lower = question.lower()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(question)},
        {"role": "user", "content": question},
    ]
    tool_history: list[dict[str, Any]] = []
    final_text = ""

    while len(tool_history) < MAX_TOOL_CALLS:
        response_json = call_llm(messages, TOOLS)
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            fail("LLM response does not contain choices")

        message = choices[0].get("message", {})
        assistant_content = text_from_content(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []

        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            for tool_call in tool_calls:
                if len(tool_history) >= MAX_TOOL_CALLS:
                    break
                function = tool_call.get("function", {})
                tool_name = function.get("name", "")
                args = parse_tool_arguments(function.get("arguments", "{}"))

                if tool_name == "query_api" and "include_auth" not in args and (
                    "without an authentication header" in question_lower
                    or "without authentication" in question_lower
                    or "without auth" in question_lower
                    or "missing authentication" in question_lower
                ):
                    args["include_auth"] = False

                result = execute_tool(tool_name, args)
                tool_history.append({"tool": tool_name, "args": args, "result": result})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", ""),
                        "content": result,
                    }
                )
            continue

        final_text = assistant_content
        break

    if not final_text:
        response_json = call_llm(messages, None)
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            fail("LLM response does not contain choices")
        final_text = text_from_content(choices[0].get("message", {}).get("content") or "")

    answer, source = parse_final_answer(final_text)
    if not answer:
        fail("LLM returned an empty answer")

    output: dict[str, Any] = {
        "answer": answer,
        "tool_calls": tool_history,
    }
    if source:
        output["source"] = source

    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
