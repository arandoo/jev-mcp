# CLAUDE.md — jev-mcp

## What this is
A lean Python (mcp SDK 2.x, MCPServer) server wrapping TypeSafe AI's Jev decision model
(`POST https://jev-ai.pro/api/v1/systemone`). Author tag: JPMC7.

## Ground rules
- Keep it simple, don't over-engineer. Two tools are enough for v0.1.
- The API contract is in `src/jev_mcp/client.py` docstring and README. If
  anything is unclear, re-fetch https://docs.typesafe.ai/api before guessing —
  the product is a week old and may change. Note the docs site and the live
  API host differ: docs at `docs.typesafe.ai`, calls go to `jev-ai.pro/api`.
- stdio transport only (local use). No HTTP transport unless asked.
- Never log or echo `JEV_AI_API_KEY`.
- All new behaviour needs a mocked test in `tests/` (respx, no real network).

## Layout
- `src/jev_mcp/client.py`  — httpx client, retry on 429/529, actionable errors
- `src/jev_mcp/server.py`  — FastMCP tools: `jev_evaluate`, `jev_route`
- `tests/test_server.py`   — mocked smoke tests

## Commands
- `uv pip install -e ".[dev]"`
- `pytest`
- `npx @modelcontextprotocol/inspector jev-mcp`

## Definition of done for v0.1
1. `pytest` green
2. Inspector lists both tools and `jev_evaluate` returns a real answer with a live key
3. Registered in Claude Desktop / Claude Code (see README)

## Possible next steps (only if asked)
- `jev_score_urgency` convenience tool
- Batch evaluate (list of states)
- Streamable HTTP transport for a shared/team server
