# KB MCP Server — Design

Date: 2026-10-08
Status: agreed in brainstorming; awaiting review of this written form

## Purpose

A remote MCP server that lets Claude (claude.ai web/mobile, including **voice mode**) understand and research a project knowledge base (KB) stored in a GitHub repo. The KB is the OpenKCM project's working memory: meeting transcripts, architecture decisions, findings, proposed architectures, mentor requirements. The user talks to Claude live (voice), and Claude must be able to orient itself (overall context + current focus), dig into a specific flow (e.g. the AWS KMS integration), recall what the mentor required, and walk through proposed designs. Comparing the KB against fresh external documentation is done by Claude's own web search — the server is KB-only.

## Users

- Primary: the repo owner (single user).
- Stretch: the mentor, with his own connector token. Supported for free by allowing several bearer tokens (`MCP_BEARER_TOKENS` is a comma-separated list).

## Decisions

| Topic | Decision |
|---|---|
| Access | Read-only. No write tools. Writes are a possible future integration. |
| KB source | Exactly one GitHub repo + branch, fixed by env vars. Tools take paths, never repo names. |
| KB shape | Mostly Markdown. A root `INDEX.md` plus per-folder indexes, maintained by a separate skill (out of scope here). |
| Index coupling | **Approach 3**: thin file tools + one orientation tool (`get_context`). The server never parses index contents. |
| Freshness | Always live: every tool call reads GitHub. A cached mode with a manual `refresh` tool is a future improvement; the GitHub access lives behind one reader class so a cache can wrap it. |
| Language / SDK | Python, official `mcp` SDK **2.x** (`mcp.server.mcpserver.MCPServer` — `FastMCP` was renamed in 2.x). |
| Transport | Streamable HTTP, stateless, JSON responses, endpoint `/mcp`. |
| Auth | Claude connector **Request headers** (beta, confirmed available on the user's account). Connector set to "No sign-in" with header `Authorization: Bearer <token>`. Server compares against `MCP_BEARER_TOKENS` in constant time. A bare token without the `Bearer ` prefix is accepted too, because the connector sends the value exactly as typed. |
| OAuth probe | Claude's add-connector dialog probes for OAuth. The server returns a plain 401 (no `WWW-Authenticate` resource metadata) and 404 on `/.well-known/*`, so the dialog reports "couldn't determine sign-in" and the user continues manually with the header. |
| GitHub credential | Fine-grained PAT, only the KB repo, `Contents: Read-only`. Lives only in the server's env; Claude never sees it. |
| Hosting | Render free web service (native Python runtime, `render.yaml` blueprint). No Dockerfile. |
| Cold start | Accepted for now: user opens `/health` before a voice session. Free uptime pinger hitting `/health` is a future improvement. `/health` never calls GitHub. |
| Tests | None by default (user's global rule). Each implementation unit ends with a manual verification step. |

## Architecture

```
Claude (web / mobile / voice)
   │  HTTPS, Authorization: Bearer <token>
   ▼
Render web service ── uvicorn ── BearerAuth (ASGI) ── MCPServer streamable-HTTP app (/mcp, /health)
                                                            │
                                                         tools.py   (formatting for voice, pagination)
                                                            │
                                                     GitHubReader   (only code that talks to GitHub)
                                                            │  REST API, read-only PAT
                                                            ▼
                                                  GitHub: KB repo @ branch
```

Units:

- `config.py` — loads and validates env vars; fails fast at startup with a clear message.
- `auth.py` — ASGI middleware guarding `/mcp`; `/health` and everything else pass through.
- `errors.py` — `KBError` hierarchy, subclassing the SDK's `ToolError` so messages reach the model instead of a generic crash.
- `paths.py` — path normalization; resolves `..` inside the KB (so relative links from per-folder indexes work) and rejects any path that climbs above the root; the only place paths are cleaned.
- `github_reader.py` — `read_text`, `get_tree`, `list_dir`, `snapshot_text_files` (tarball, one request).
- `search.py` — pure ranking/snippet function over `(path, text)` pairs.
- `tools.py` — the four MCP tools and their text formatting.
- `scripts/mcp_call.sh` — calls one tool over HTTP for manual verification.
- `app.py` — wiring: settings → reader → server → tools → `/health` → auth → ASGI `app`.

## Tools (v1, all `readOnlyHint`)

- `get_context()` — the root index (`KB_ROOT_INDEX`, default `INDEX.md`). If missing, it returns a tool error telling Claude to use `list_dir`/`search`. Server instructions tell Claude to call it first.
- `list_dir(path="")` — direct children of a folder; folders end with `/`, files show size.
- `read_file(path, offset=0, max_chars=15000)` — text of one file; paginated (cap 50 000 chars per call) with an explicit "continues — call read_file with offset=N" footer. Binary files are refused with a clear message.
- `search(query, path="", limit=10)` — downloads the repo tarball (1 API call, always live), keeps text files ≤ 2 MB, requires every query word to appear in the file's text or path, ranks by hit count (path hits weigh more), returns up to 3 snippet lines per file.

GitHub code-search API is deliberately not used: its index lags behind pushes, which breaks "always live".

## Error handling

All anticipated failures raise a `KBError` subclass with a sentence Claude can act on:

- invalid path / folder passed to `read_file` / file passed to `list_dir` → `InvalidPathError`
- 404 on a file → `KBNotFoundError` ("use list_dir or search")
- 404 on the branch itself (git tree, tarball) → `KBUnavailableError` ("check KB_REPO, KB_BRANCH and the token's repository access"), because GitHub answers 404 when a token can't see a private repo
- 401/403/429, network errors, other GitHub errors → `KBUnavailableError`
- Every GitHub error, 404s included, is logged server-side with its status and GitHub's message
- Config errors → process refuses to start (`ConfigError`), visible in Render logs.

Tokens and file contents are never logged. The `httpx` logger is set to WARNING because its INFO request lines include the tarball redirect URL, which carries a temporary download token.

## Config (env vars)

| Var | Required | Default | Notes |
|---|---|---|---|
| `GITHUB_TOKEN` | yes | — | fine-grained PAT, Contents read-only, KB repo only |
| `KB_REPO` | yes | — | `owner/name` |
| `KB_BRANCH` | no | `main` | |
| `KB_ROOT_INDEX` | no | `INDEX.md` | path of the entry-point index |
| `MCP_BEARER_TOKENS` | yes | — | comma-separated; each ≥ 32 chars |
| `PORT` | Render sets it | — | used only in the start command |

## Risks

1. **Custom connectors in voice mode are not documented.** Voice mode docs say connected tools work "the same way … as in text chat" but only name first-party connectors. Mitigation: deploy and test in voice as soon as the first real tool (`get_context`) exists, before building the rest.
2. Request-header auth is a beta feature. Fallback: secret path segment in the URL (not built unless needed).
3. Render free tier cold start (30–60 s) can stall the first voice tool call. Accepted for now.

## Future improvements (out of scope for v1)

- Uptime pinger on `/health` to avoid cold starts.
- Cached mode keyed on commit SHA + `refresh` tool.
- Mentor access: add his token to `MCP_BEARER_TOKENS` (no code change).
- `get_focus_bundle(topic)` — one call returning every doc relevant to a flow.
- Write path (e.g. save a voice-session note) — would need a write-scoped token and its own design.
