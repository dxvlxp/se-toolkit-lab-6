# AGENT.md

## Overview

This repository contains a minimal CLI agent for Lab 6 Task 1.

The agent:
- accepts a question as the first command-line argument;
- sends the question to an OpenAI-compatible LLM API;
- prints a single JSON object to stdout.

Example output:

```json
{"answer": "Representational State Transfer.", "tool_calls": []}
