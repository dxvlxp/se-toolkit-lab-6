from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class _FrameworkHandler(BaseHTTPRequestHandler):
    llm_call_count = 0

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers["Content-Length"])
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        assert self.path == "/v1/chat/completions"
        assert payload["model"] == "test-model"

        _FrameworkHandler.llm_call_count += 1

        if _FrameworkHandler.llm_call_count == 1:
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-read-main",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "backend/app/main.py"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
            assert tool_messages, "Expected a tool message in the second LLM request"
            assert "FastAPI" in tool_messages[-1]["content"]

            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "The backend uses FastAPI.",
                                    "source": "backend/app/main.py",
                                }
                            )
                        }
                    }
                ]
            }

        encoded = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class _QueryAPIHandler(BaseHTTPRequestHandler):
    llm_call_count = 0

    def do_GET(self) -> None:  # noqa: N802
        assert self.path == "/items/"
        assert self.headers.get("Authorization") == "Bearer lms-test-key"

        response_body = [
            {"id": 1, "title": "Item 1"},
            {"id": 2, "title": "Item 2"},
            {"id": 3, "title": "Item 3"},
        ]
        encoded = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers["Content-Length"])
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        assert self.path == "/v1/chat/completions"
        assert payload["model"] == "test-model"

        _QueryAPIHandler.llm_call_count += 1

        if _QueryAPIHandler.llm_call_count == 1:
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-query-items",
                                    "type": "function",
                                    "function": {
                                        "name": "query_api",
                                        "arguments": json.dumps(
                                            {
                                                "method": "GET",
                                                "path": "/items/",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
            assert tool_messages, "Expected a tool message in the second LLM request"
            assert '"status_code": 200' in tool_messages[-1]["content"]
            assert '"body_count": 3' in tool_messages[-1]["content"]

            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "There are 3 items in the database.",
                                    "source": "",
                                }
                            )
                        }
                    }
                ]
            }

        encoded = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_agent_uses_read_file_for_framework_question() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    _FrameworkHandler.llm_call_count = 0
    server = HTTPServer(("127.0.0.1", 0), _FrameworkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["LLM_API_KEY"] = "test-key"
        env["LLM_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
        env["LLM_MODEL"] = "test-model"
        env["LMS_API_KEY"] = "unused-in-this-test"

        result = subprocess.run(
            [sys.executable, "agent.py", "What framework does the backend use?"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        assert data["answer"] == "The backend uses FastAPI."
        assert data["source"] == "backend/app/main.py"
        assert any(call["tool"] == "read_file" for call in data["tool_calls"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_agent_uses_query_api_for_database_count_question() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    _QueryAPIHandler.llm_call_count = 0
    server = HTTPServer(("127.0.0.1", 0), _QueryAPIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["LLM_API_KEY"] = "test-key"
        env["LLM_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
        env["LLM_MODEL"] = "test-model"
        env["LMS_API_KEY"] = "lms-test-key"
        env["AGENT_API_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"

        result = subprocess.run(
            [sys.executable, "agent.py", "How many items are in the database?"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        assert data["answer"] == "There are 3 items in the database."
        assert any(call["tool"] == "query_api" for call in data["tool_calls"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
