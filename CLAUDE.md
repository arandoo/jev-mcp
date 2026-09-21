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

## Git (solo flow — one writer, trunk-based)
This repo has exactly one writer. There is no second human to coordinate with,
so there is no `develop`, no long-lived feature branches, and no PRs.

- `main` is the only shared branch. Every commit on it has passed the checks.
- Work happens on a short-lived local branch in its own worktree, created off
  `main` and placed outside the repo so tooling never scans a nested copy:
  `git worktree add ../jev-mcp.wt/<slug> -b feat/<slug> main`
- The primary checkout never leaves `main`. Merges and pushes happen there.
  `git worktree list` is the complete inventory of work in flight; anything on
  it that isn't the current item is an orphan from a crashed run — resume it if
  it holds work worth keeping, otherwise remove it.
- Land work by removing both: `git worktree remove ../jev-mcp.wt/<slug> &&
  git branch -d feat/<slug>`.
- Before merging: full `pytest`, plus one fresh-context review of the diff by a
  different agent than wrote it. No PR does not mean no review.
- Fast-forward merges only — keep `git log --graph` on `main` readable.
- Never force-push or delete `main`. Never commit to `main` without the checks
  having run on that commit.

### Releases
A release is an annotated tag on a commit of `main`, pushed, with notes as a
GitHub Release. Nothing merges between branches to produce one.
`git tag -a v0.2.0 -m "0.2.0: <summary>" && git push origin v0.2.0`

The agent recommends; the owner approves. Never tag on the agent's judgement
alone. Cut one when: a consumer needs a version to pin; observable behaviour
changed; 3+ items have closed since the last release; or 2 weeks of unreleased
work have accumulated. Patch for fixes, minor for anything a consumer can
observe. Tags are immutable — a bad tag means a new patch version, never a
moved tag.

Owner-approval required: tags, GitHub releases, CI config, repo settings.

## Layout
- `src/jev_mcp/client.py`  — httpx client, retry on 429/529, actionable errors,
  `.env` fallback for the key (env vars win)
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
