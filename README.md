# jev-mcp

MCP server that exposes TypeSafe AI's **Jev** decision model to Claude
(Desktop, Code, Cowork). Two tools:

| Tool | Use |
|---|---|
| `jev_evaluate` | Full System One call: state + any mix of `noul` / `choice` / `score` questions |
| `jev_route` | One choice question + confidence threshold → `{choice, confidence, escalate}` |

## Setup

```bash
uv venv && source .venv/bin/activate      # or python -m venv .venv
uv pip install -e ".[dev]"                # or pip install -e ".[dev]"
cp .env.example .env                      # add your JEV_AI_API_KEY
pytest                                    # mocked, no network needed
```

## Try it

```bash
npx @modelcontextprotocol/inspector jev-mcp
```

## Add to Claude Desktop

`claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "jev": {
      "command": "/absolute/path/to/jev-mcp/.venv/bin/jev-mcp",
      "env": { "JEV_AI_API_KEY": "your-key" }
    }
  }
}
```

## Add to Claude Code

```bash
claude mcp add jev -e JEV_AI_API_KEY=your-key -- /absolute/path/to/jev-mcp/.venv/bin/jev-mcp
```

## API reference

Docs: https://docs.typesafe.ai/api — `POST /v1/systemone`, verified 2026-09-21.
Live API base: `https://jev-ai.pro/api` (override with `JEV_AI_API_BASE`).
Note the docs host and the API host differ; `api.typesafe.ai` rejects `sk_jev-ai_` keys.
Jev 1.13 limits: 64k total context, 32k for state + longest question.
