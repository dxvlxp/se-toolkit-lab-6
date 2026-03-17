# Task 1 Plan

## LLM provider and model

I will use the OpenAI-compatible Qwen Code API with the `qwen3-coder-plus` model.

Why:
- it is the recommended option in the lab materials;
- it supports the OpenAI-compatible chat completions API;
- it will also be suitable for the next tasks when tool calling is added.

## Agent structure

The agent will be a small CLI program in `agent.py`.

Flow:
1. Read the user question from the first command-line argument.
2. Load LLM configuration from `.env.agent.secret`.
3. Send a single chat completion request to the configured LLM.
4. Extract the text answer from the response.
5. Print exactly one JSON object to stdout:
   - `answer`: final text answer
   - `tool_calls`: empty array for Task 1

## Technical decisions

- Use `httpx` for the HTTP request because it is already available in the repository.
- Keep the system prompt minimal.
- Print only JSON to stdout.
- Print all errors/debug information to stderr.
- Exit with code 0 on success and non-zero on failure.

## Test plan

Create one regression test that:
1. starts a local mock HTTP server;
2. runs `agent.py` as a subprocess;
3. injects `LLM_API_KEY`, `LLM_API_BASE`, and `LLM_MODEL` through environment variables;
4. validates that stdout is valid JSON;
5. checks that `answer` and `tool_calls` are present;
6. checks that `tool_calls` is an empty list.
