# jev-mcp

MCP server that exposes TypeSafe AI's **Jev** decision model to Claude
(Desktop, Code, Cowork). Two tools:

| Tool | Use |
|---|---|
| `jev_evaluate` | Full System One call: state + any mix of `noul` / `choice` / `score` questions |
| `jev_route` | One choice question + confidence threshold → `{choice, confidence, escalate}` |

## Setup

Needs Python 3.11+ (macOS ships 3.9, so name the version explicitly).

```bash
python3.12 -m venv .venv                  # or: uv venv --python 3.12
.venv/bin/pip install -e ".[dev]"         # or: uv pip install -e ".[dev]"
cp .env.example .env                      # add your JEV_AI_API_KEY
chmod 600 .env                            # it holds a secret
.venv/bin/pytest                          # mocked, no network needed
```

The server reads `JEV_AI_API_KEY` from the environment, falling back to this
`.env`. Keeping the key here means it lives in one file instead of being copied
into every MCP client's config — so the registrations below carry no secrets.

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
      "args": []
    }
  }
}
```

Fully quit Claude Desktop (⌘Q) and reopen — the config is only read at launch.
Add an `"env": { "JEV_AI_API_KEY": "..." }` block only if you want to override
the key in `.env`; environment variables take precedence.

## Add to Claude Code

```bash
claude mcp add jev -s user -- /absolute/path/to/jev-mcp/.venv/bin/jev-mcp
```

`-s user` makes it available in every project; the default (`local`) registers
it only for the current directory. Restart the session afterwards — MCP servers
are loaded at startup.

## API reference

Docs: https://docs.typesafe.ai/api — `POST /v1/systemone`, verified 2026-09-21.
Live API base: `https://jev-ai.pro/api` (override with `JEV_AI_API_BASE`).
Note the docs host and the API host differ; `api.typesafe.ai` rejects `sk_jev-ai_` keys.
Jev 1.13 limits: 64k total context, 32k for state + longest question.
