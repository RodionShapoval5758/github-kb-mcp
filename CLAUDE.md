# KB MCP Server

Read-only remote MCP server that gives Claude (claude.ai web, mobile and voice mode) live access to one GitHub-hosted project knowledge base. Today that is the OpenKCM KB: meeting transcripts, architecture decisions, findings, proposed designs and mentor requirements. The goal is to talk to Claude by voice and have it understand the project's context and current focus. It should also be able to research a specific flow (e.g. the AWS KMS integration), recall what the mentor required, and walk through proposed architectures.

Design spec: `docs/superpowers/specs/2026-10-08-kb-mcp-design.md`. Implementation plan: `docs/superpowers/plans/2026-10-08-kb-mcp.md`.

## How it works

```
Claude ──HTTPS, Authorization: Bearer──▶ Render (uvicorn) ──▶ BearerAuth ──▶ MCP streamable-HTTP app (/mcp, /health)
        ──▶ tools ──▶ GitHubReader ──▶ GitHub REST API (read-only PAT) ──▶ KB repo @ branch
```

- One KB repo and branch, fixed by env vars. Tools take paths only.
- Always live: every tool call reads GitHub. Nothing is cached or stored.
- A separate skill organizes and indexes the KB (root `INDEX.md` plus per-folder indexes). This server never parses index contents; Claude reads them.

## Tools

| Tool | Purpose |
|---|---|
| `get_context()` | Root index: project overview, current focus, where things live. Claude calls it first. |
| `list_dir(path="")` | Direct children of a folder. |
| `read_file(path, offset=0, max_chars=15000)` | File text, paginated (at most 50 000 chars per call). |
| `search(query, path="", limit=10)` | All-words match over file names and text, using one tarball download. Ranked, with snippets. |

## Layout

```
src/kb_mcp/
  app.py            wiring; exposes the ASGI `app`
  config.py         env → Settings, fails fast
  auth.py           BearerAuth ASGI middleware for /mcp
  errors.py         KBError hierarchy (subclasses the SDK's ToolError)
  paths.py          normalize_path, the only path sanitizer
  github_reader.py  the only code that talks to GitHub
  logging_setup.py  log format, LOG_LEVEL, uvicorn log routing
  search.py         pure ranking and snippets
  tools.py          MCP tools and voice-friendly formatting
scripts/mcp_call.sh call one tool over HTTP for manual checks
render.yaml         Render blueprint (free web service)
```

## Commands

- Install: `uv sync`
- Run locally: `uv run --env-file .env uvicorn kb_mcp.app:app --port 8000`
- Call a tool: `MCP_TOKEN=<token> scripts/mcp_call.sh read_file '{"path":"INDEX.md"}'`
- Generate a bearer token: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`

## Config

`GITHUB_TOKEN` (fine-grained PAT, KB repo only, Contents read-only), `KB_REPO` (`owner/name`), `KB_BRANCH` (default `main`), `KB_ROOT_INDEX` (default `INDEX.md`), `MCP_BEARER_TOKENS` (comma-separated, each at least 32 chars), `LOG_LEVEL` (default `INFO`). Template: `.env.example`.

## Invariants and gotchas

- **Read-only.** Only GET requests to GitHub, and no write tools. Writes would need their own design.
- **MCP SDK 2.x:** use `from mcp.server.mcpserver import MCPServer`. `mcp.server.fastmcp` no longer exists.
- `streamable_http_app(..., host="0.0.0.0")` is required. The default localhost host enables a Host-header check that rejects the Render hostname.
- Register `custom_route`s before calling `streamable_http_app()`.
- Anticipated failures raise a `KBError` subclass, and its message is shown to Claude. Any other exception reaches Claude only as "Error executing tool".
- The 401 response deliberately has no `WWW-Authenticate` OAuth metadata, so Claude's connector dialog doesn't try OAuth. Bare tokens (no `Bearer ` prefix) are accepted because the connector sends the header value exactly as typed.
- Tool output is short plain text (`structured_output=False`), because conversations are often spoken. Keep the tool count small and the descriptions explicit.
- Never log tokens, file contents or GitHub redirect URLs. Log lines go to stdout; `docs/docker-run-and-test.md` explains how to read them.
- The GitHub startup check is non-fatal on purpose: the Docker smoke test starts the container with a dummy token and expects `/health` to answer.
- Don't use GitHub's code-search API for `search`. Its index lags behind pushes.
- The Render free tier sleeps after ~15 min idle. Open `/health` before a voice session.

## Future improvements (not v1)

- Uptime pinger on `/health`.
- Cached mode keyed on the commit SHA, plus a `refresh` tool.
- Mentor access through an extra token in `MCP_BEARER_TOKENS`.
- `get_focus_bundle(topic)`.
- A write path.
