# Agent Architecture

## Overview

This document describes the architecture of the documentation agent implemented in `agent.py`. The agent is a CLI tool that answers questions by navigating the project wiki using function calling with an LLM.

## Agentic Loop

The agent implements an **agentic loop** that allows the LLM to decide which tools to call, execute them, and reason about the results:

```
Question → LLM (with tool schemas)
    ↓
Has tool_calls?
    ├─ Yes → Execute tools → Append results → Back to LLM (max 10 iterations)
    └─ No  → Extract answer + source → Output JSON → Exit
```

### Implementation Flow

1. **Send question to LLM** with tool schemas defined
2. **Check for tool calls** in the response
3. **If tool calls exist:**
   - Execute each tool with the provided arguments
   - Log the tool call (tool name, args, result)
   - Append tool results to messages as `role: "tool"`
   - Continue to next iteration
4. **If no tool calls:**
   - Extract the final answer text
   - Extract the source reference from the answer
   - Output JSON and exit
5. **Max iterations:** Stop after 10 tool calls to prevent infinite loops

## Tools

The agent has two tools available:

### `read_file(path)`

Reads the contents of a file from the project repository.

**Parameters:**
- `path` (string): Relative path from project root (e.g., `wiki/git-workflow.md`)

**Returns:**
- File contents as a string
- Error message if file doesn't exist or path is invalid

**Security:**
- Rejects absolute paths
- Rejects paths containing `..` (traversal prevention)
- Verifies resolved path is within project directory

### `list_files(path)`

Lists files and directories at a given path.

**Parameters:**
- `path` (string): Relative directory path from project root (e.g., `wiki`)

**Returns:**
- Newline-separated listing of entries (directories have `/` suffix)
- Error message if path doesn't exist or is not a directory

**Security:**
- Same path validation as `read_file`

## Tool Schemas

Tools are registered with the LLM using OpenAI function-calling schema format:

```python
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
                        "description": "Relative path from project root"
                    }
                },
                "required": ["path"]
            }
        }
    },
    # ... list_files schema
]
```

## System Prompt

The system prompt instructs the LLM on how to use the tools effectively:

```
You are a helpful assistant that answers questions using the project wiki.

You have access to two tools:
- list_files(path): List files and directories at a given path
- read_file(path): Read contents of a file

Strategy:
1. Use list_files to discover relevant wiki files in the wiki/ directory
2. Use read_file to read specific files and find the answer
3. Always include the source reference in your answer using format: wiki/filename.md#section-anchor

When answering:
- Be concise and direct
- Always provide a source reference
- If you cannot find the answer, say so clearly
```

## Output Format

The agent outputs JSON with three fields:

```json
{
  "answer": "The answer text",
  "source": "wiki/git-workflow.md#resolving-merge-conflicts",
  "tool_calls": [
    {
      "tool": "list_files",
      "args": {"path": "wiki"},
      "result": "git-workflow.md\n..."
    },
    {
      "tool": "read_file",
      "args": {"path": "wiki/git-workflow.md"},
      "result": "..."
    }
  ]
}
```

### Fields

- **`answer`** (string): The final answer from the LLM
- **`source`** (string): The wiki section reference extracted from the answer (format: `wiki/filename.md#section-anchor`)
- **`tool_calls`** (array): All tool calls made during the agentic loop
  - `tool`: Tool name (`read_file` or `list_files`)
  - `args`: Arguments passed to the tool
  - `result`: Result returned by the tool

## Path Security

Both tools implement path validation to prevent accessing files outside the project directory:

```python
def validate_path(path_str: str) -> Path:
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
```

## Error Handling

- **Tool errors:** Return error message as tool result, continue loop
- **LLM API errors:** Print error to stderr and exit with non-zero code
- **Max iterations:** Return partial answer with tool_calls log
- **Invalid paths:** Return error message from tool (not a failure)

## Usage

```bash
# Set environment variables
export LLM_API_KEY="your-api-key"
export LLM_API_BASE="https://api.example.com/v1"
export LLM_MODEL="your-model"

# Run the agent
python agent.py "How do you resolve a merge conflict?"
```

## Testing

Run the unit tests:

```bash
pytest backend/tests/unit/test_agent_task_2.py
```

Tests verify:
1. Agent outputs required JSON fields (`answer`, `tool_calls`, `source`)
2. Agent calls `list_files` when asked about wiki files
3. Agent calls `read_file` when asked about specific topics
