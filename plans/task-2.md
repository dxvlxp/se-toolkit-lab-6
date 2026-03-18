# Plan for Task 2: The Documentation Agent

## Overview

Task 2 extends the basic LLM CLI from Task 1 into a full **agentic system** that can navigate the project wiki using tools (`read_file`, `list_files`) to find answers.

## Tool Schemas

### Approach

Define tools using the OpenAI function-calling schema format. Each tool will have:
- `name`: The function name (`read_file` or `list_files`)
- `description`: What the tool does
- `parameters`: JSON Schema defining required arguments

### Schema Definitions

```python
TOOLS = [
    {
        "name": "read_file",
        "description": "Read contents of a file from the project repository",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from project root"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_files",
        "description": "List files and directories at a given path",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory path from project root"}
            },
            "required": ["path"]
        }
    }
]
```

## Tool Implementations

### `read_file(path)`

- Validate path does not contain `../` traversal
- Resolve path relative to project root
- Check resolved path is within project directory
- Return file contents or error message

### `list_files(path)`

- Validate path does not contain `../` traversal
- Resolve path relative to project root
- Check resolved path is within project directory and is a directory
- Return newline-separated listing of entries

## Path Security

Security checks for both tools:

1. **Reject absolute paths**: Path must be relative
2. **Reject traversal**: Path must not contain `..`
3. **Resolve and verify**: After resolving, check the path starts with project root
4. **Use `Path.resolve()`**: Get canonical absolute path for comparison

```python
def validate_path(path: str) -> Path:
    # Reject absolute paths
    if os.path.isabs(path):
        raise ValueError("Path must be relative")
    
    # Reject traversal
    if ".." in path:
        raise ValueError("Path traversal not allowed")
    
    # Resolve and verify
    resolved = (PROJECT_ROOT / path).resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        raise ValueError("Path outside project directory")
    
    return resolved
```

## Agentic Loop

### Flow

```
Question → LLM (with tool schemas)
    ↓
Has tool_calls?
    ├─ Yes → Execute tools → Append results → Back to LLM (max 10 iterations)
    └─ No  → Extract answer + source → Output JSON → Exit
```

### Implementation

```python
def run_agent_loop(question: str, max_iterations: int = 10) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question}
    ]
    
    tool_calls_log = []
    
    for _ in range(max_iterations):
        response = call_llm(messages, tools=TOOLS)
        
        # Check for tool calls
        if response.get("tool_calls"):
            for tool_call in response["tool_calls"]:
                result = execute_tool(tool_call)
                tool_calls_log.append({
                    "tool": tool_call["function"]["name"],
                    "args": json.loads(tool_call["function"]["arguments"]),
                    "result": result
                })
                # Append tool result to messages
                messages.append({...})
            continue
        else:
            # Final answer
            answer = extract_text(response)
            source = extract_source(answer)  # LLM should include source in response
            return {
                "answer": answer,
                "source": source,
                "tool_calls": tool_calls_log
            }
    
    # Max iterations reached
    return {
        "answer": "Reached maximum tool calls",
        "source": "",
        "tool_calls": tool_calls_log
    }
```

## System Prompt Strategy

The system prompt will instruct the LLM to:

1. Use `list_files` to discover wiki files in relevant directories
2. Use `read_file` to read specific files and find answers
3. Always include a source reference in the final answer (file path + section anchor)
4. Be concise and direct

Example:

```
You are a helpful assistant that answers questions using the project wiki.

You have access to two tools:
- list_files(path): List files in a directory
- read_file(path): Read contents of a file

Strategy:
1. First use list_files to discover relevant wiki files
2. Then use read_file to find the specific answer
3. Include the source reference (file path + section anchor) in your answer

Always provide the source in format: wiki/filename.md#section-anchor
```

## Output Format

```json
{
  "answer": "The answer text",
  "source": "wiki/git-workflow.md#resolving-merge-conflicts",
  "tool_calls": [
    {"tool": "list_files", "args": {"path": "wiki"}, "result": "..."},
    {"tool": "read_file", "args": {"path": "wiki/git-workflow.md"}, "result": "..."}
  ]
}
```

## Error Handling

- **Tool errors**: Return error message as tool result, continue loop
- **LLM errors**: Fail with error message to stderr
- **Max iterations**: Return partial answer with tool_calls log
- **Invalid paths**: Return error message from tool

## Testing Strategy

Two regression tests:

1. **Test `read_file` usage**: Question about merge conflicts should trigger `read_file` and reference `wiki/git-workflow.md`
2. **Test `list_files` usage**: Question about wiki files should trigger `list_files`

Tests will:
- Run `agent.py` with mock LLM server
- Verify `tool_calls` contains expected tools
- Verify `source` field contains expected file reference
