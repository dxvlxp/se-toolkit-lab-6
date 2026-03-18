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
            "choices": [
                {
                    "message": {
                        "content": "Representational State Transfer."
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
