# AGENT.md

## Overview

This repository contains a CLI agent that answers questions about both the project repository and the running backend system. The agent is designed for Lab 6 Task 3 and extends the Task 2 documentation agent with a live backend tool called `query_api`.

The agent accepts a user question as the first CLI argument, runs an agentic loop with function calling, and prints exactly one JSON object to stdout. The final output always contains:
- `answer`
- `tool_calls`

It may also contain:
- `source`

The `source` field is included when there is a meaningful file reference to report, such as a wiki page or a source code file.

## Tools

The agent has three tools:

### `list_files`
Lists files and directories inside the repository. This is useful when the model needs to discover the correct path before reading a file. It is especially helpful for the wiki and for `backend/app/routers`.

### `read_file`
Reads a text file from the repository. This is used for:
- wiki questions,
- source code inspection,
- Docker and architecture questions,
- ETL reasoning,
- bug diagnosis after reproducing an API error.

The implementation prevents path traversal outside the repository root.

### `query_api`
Calls the running backend API. It is used for:
- current counts and live data,
- real HTTP status codes,
- authentication behavior,
- reproducing endpoint crashes before reading the source code.

`query_api` authenticates with `LMS_API_KEY`, which is different from `LLM_API_KEY`. The backend URL is configured through `AGENT_API_BASE_URL`. If it is not set, the agent falls back to `http://localhost:42002`.

## Agent loop

The loop follows the standard function-calling pattern:
1. send the question and tool schemas to the LLM;
2. if the LLM requests tools, execute them;
3. append tool results back into the conversation;
4. repeat until the model returns a final answer or the tool-call limit is reached.

The implementation handles the common `content: null` tool-calling case by using `(message.get("content") or "")`.

## Tool selection strategy

The prompt strongly separates three evidence sources:
- wiki/docs → `read_file` / `list_files`
- source code and architecture → `read_file` / `list_files`
- live system state and endpoint behavior → `query_api`

For bug-diagnosis questions, the agent is instructed to first reproduce the failure with `query_api` and only then inspect the relevant file with `read_file`. This is important for hidden multi-step questions.

I also added small question-specific hints for common benchmark patterns such as:
- framework detection from `backend/app/main.py`
- router discovery in `backend/app/routers`
- analytics bug diagnosis in `backend/app/routers/analytics.py`
- ETL idempotency in `backend/app/etl.py`

## Lessons learned

The main failure modes in this lab are usually not syntax bugs, but reasoning/tool-selection bugs:
- the model may answer from prior knowledge without using a tool;
- it may choose `read_file` when the question actually asks about live runtime state;
- it may reproduce an API error but forget to inspect the source code afterward.

To reduce those mistakes, I made the tool descriptions explicit and added task-specific routing hints in the system prompt. I also made `query_api` optionally support unauthenticated requests through `include_auth=false`, which is useful for questions about missing authentication headers.

## Benchmark result

Initial local score:
- Pending — replace after the first real `uv run run_eval.py`.

Final local score:
- Pending — replace after the final successful benchmark run.
