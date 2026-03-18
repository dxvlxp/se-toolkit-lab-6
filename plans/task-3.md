# Task 3 Plan

## Goal

Upgrade the Task 2 documentation agent into a system agent that can answer both repository questions and live backend questions.

## Tool design

I will keep the Task 2 agentic loop and add one more tool:

- `query_api(method, path, body, include_auth?)`

Core parameters required by the task:
- `method`
- `path`
- `body` (optional)

I also add an optional `include_auth` boolean to handle questions about missing authentication headers. By default, the tool sends the backend API key.

The tool returns a JSON string with:
- `status_code`
- `body`

When the body is a list, it also returns `body_count` to make counting questions easier for the model.

## Authentication

The agent will use two different secrets:
- `LLM_API_KEY` for the LLM provider
- `LMS_API_KEY` for backend API authentication

The backend base URL will be read from `AGENT_API_BASE_URL`, with a default fallback to `http://localhost:42002`.

## Prompt strategy

The system prompt will explicitly tell the model:
- use `read_file` / `list_files` for wiki and source code;
- use `query_api` for runtime state, counts, status codes, and endpoint failures;
- for bug diagnosis, first reproduce the error with `query_api`, then inspect the source with `read_file`.

I will also add lightweight question-specific hints for common benchmark patterns:
- framework → `backend/app/main.py`
- routers → `backend/app/routers`
- completion-rate / top-learners → `backend/app/routers/analytics.py`
- ETL idempotency → `backend/app/etl.py`

## Benchmark diagnosis

Initial local benchmark score:
- Pending — run `uv run run_eval.py` and replace this line with the real result.

First failing questions:
- Pending — fill after the first benchmark run.

Iteration strategy:
1. Run `uv run run_eval.py`.
2. Fix the first failed question only.
3. Improve tool descriptions or system prompt if the model chooses the wrong tool.
4. Improve tool implementation if a tool returns weak or incomplete evidence.
5. Re-run until all 10 local questions pass.
