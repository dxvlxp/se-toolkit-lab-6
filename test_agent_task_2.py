from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class _MockHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        assert self.path == "/v1/chat/completions"

        content_length = int(self.headers["Content-Length"])
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        assert payload["model"] == "test-model"
        assert payload["messages"][-1]["content"] == "What does REST stand for?"

        response_body = {
            "choices": [{"message": {"content": "Representational State Transfer."}}]
        }

        encoded = json.dumps(response_body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_agent_outputs_required_json_fields() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["LLM_API_KEY"] = "test-key"
        env["LLM_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
        env["LLM_MODEL"] = "test-model"

        result = subprocess.run(
            [sys.executable, "agent.py", "What does REST stand for?"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

        data = json.loads(result.stdout)
        assert "answer" in data
        assert "tool_calls" in data
        assert data["answer"] == "Representational State Transfer."
        assert data["tool_calls"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class _MockHandlerWithToolCalls(BaseHTTPRequestHandler):
    """Mock LLM that responds with tool calls for testing the agentic loop."""

    _call_count = 0

    def do_POST(self) -> None:  # noqa: N802
        assert self.path == "/v1/chat/completions"

        content_length = int(self.headers["Content-Length"])
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        assert payload["model"] == "test-model"

        # Check if this is the initial question or a tool response
        messages = payload.get("messages", [])
        has_tool_response = any(m.get("role") == "tool" for m in messages)

        if not has_tool_response:
            # First call: return tool call for list_files
            _MockHandlerWithToolCalls._call_count += 1
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "list_files",
                                        "arguments": json.dumps({"path": "wiki"}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            # Second call: return final answer with source
            _MockHandlerWithToolCalls._call_count += 1
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": "The wiki contains documentation about git workflow. See wiki/git-workflow.md#resolving-merge-conflicts for details."
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


def test_agent_calls_list_files_tool() -> None:
    """Test that the agent calls list_files tool when asked about wiki files."""
    repo_root = Path(__file__).resolve().parents[3]

    _MockHandlerWithToolCalls._call_count = 0

    server = HTTPServer(("127.0.0.1", 0), _MockHandlerWithToolCalls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["LLM_API_KEY"] = "test-key"
        env["LLM_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
        env["LLM_MODEL"] = "test-model"

        result = subprocess.run(
            [sys.executable, "agent.py", "What files are in the wiki?"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        assert "answer" in data
        assert "tool_calls" in data
        assert "source" in data

        # Verify list_files was called
        tool_names = [tc["tool"] for tc in data["tool_calls"]]
        assert "list_files" in tool_names, "Expected list_files to be called"

        # Verify source contains wiki reference
        assert "wiki/" in data["source"] or "wiki/" in data["answer"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


class _MockHandlerWithReadFile(BaseHTTPRequestHandler):
    """Mock LLM that responds with tool calls for read_file testing."""

    _call_count = 0

    def do_POST(self) -> None:  # noqa: N802
        assert self.path == "/v1/chat/completions"

        content_length = int(self.headers["Content-Length"])
        raw_body = self.rfile.read(content_length)
        payload = json.loads(raw_body.decode("utf-8"))

        assert payload["model"] == "test-model"

        messages = payload.get("messages", [])
        has_tool_response = any(m.get("role") == "tool" for m in messages)

        if not has_tool_response:
            # First call: return tool call for read_file
            _MockHandlerWithReadFile._call_count += 1
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps(
                                            {"path": "wiki/git-workflow.md"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        else:
            # Second call: return final answer with source
            _MockHandlerWithReadFile._call_count += 1
            response_body = {
                "choices": [
                    {
                        "message": {
                            "content": "To resolve a merge conflict, edit the conflicting file, choose which changes to keep, then stage and commit. See wiki/git-workflow.md#resolving-merge-conflicts for more details."
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


def test_agent_calls_read_file_for_merge_conflict() -> None:
    """Test that the agent calls read_file when asked about resolving merge conflicts."""
    repo_root = Path(__file__).resolve().parents[3]

    _MockHandlerWithReadFile._call_count = 0

    server = HTTPServer(("127.0.0.1", 0), _MockHandlerWithReadFile)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        env = os.environ.copy()
        env["LLM_API_KEY"] = "test-key"
        env["LLM_API_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
        env["LLM_MODEL"] = "test-model"

        result = subprocess.run(
            [sys.executable, "agent.py", "How do you resolve a merge conflict?"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        assert "answer" in data
        assert "tool_calls" in data
        assert "source" in data

        # Verify read_file was called
        tool_names = [tc["tool"] for tc in data["tool_calls"]]
        assert "read_file" in tool_names, "Expected read_file to be called"

        # Verify the file path in tool calls
        read_file_calls = [tc for tc in data["tool_calls"] if tc["tool"] == "read_file"]
        assert any(
            "git-workflow.md" in tc["args"].get("path", "") for tc in read_file_calls
        )

        # Verify source contains wiki reference
        assert "wiki/" in data["source"] or "wiki/" in data["answer"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
