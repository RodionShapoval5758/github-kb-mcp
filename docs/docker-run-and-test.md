# Task: run the KB MCP server in Docker and test it

Goal: build the image, run it as a container, and confirm that auth, the four tools and the GitHub connection all work.

Prerequisites: Docker running, `curl`, `python3`, a clone of this repo. Run every command from the repo root.

Shortcut: `Taskfile.yaml` wraps these steps. Install go-task (on Fedora: `sudo dnf install go-task`, then run `go-task`; the `task` command there is Taskwarrior). Then run `go-task run`, `go-task test`, `go-task test:kb` and `go-task stop`.

## 1. Configure

```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `GITHUB_TOKEN` | Fine-grained PAT, KB repo only, Contents read-only |
| `KB_REPO` | `owner/name` of the KB repo |
| `MCP_BEARER_TOKENS` | `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` (at least 32 chars) |

No real credentials yet? Use `GITHUB_TOKEN=dummy`, `KB_REPO=owner/repo` and any 32+ char bearer token. Parts A and B still work. Part C needs real values.

## 2. Build and run

```bash
docker build -t kb-mcp .
docker run -d --rm --name kb-mcp --env-file .env -p 8000:8000 kb-mcp
export MCP_TOKEN=<one of the MCP_BEARER_TOKENS values>
```

Pass: `docker ps --filter name=kb-mcp` lists the container as `Up`.

## 3. Tests

### A. Server and auth (no GitHub needed)

| # | Command | Expected |
|---|---|---|
| A1 | `curl -s localhost:8000/health` | `ok` |
| A2 | `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/mcp` | `401` |
| A3 | `curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/mcp -H 'Authorization: Bearer wrong'` | `401` |
| A4 | `curl -s -X POST localhost:8000/mcp -H "Authorization: Bearer $MCP_TOKEN" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'` | JSON listing `get_context`, `read_file`, `list_dir`, `search` |
| A5 | `scripts/mcp_call.sh search '{"query":"  "}'` | `Error executing tool search: search needs at least one word.` (exit 1) |
| A6 | `scripts/mcp_call.sh read_file '{"path":"../x"}'` | `Paths must stay inside the knowledge base` (exit 1) |
| A7 | `sleep 35; docker inspect -f '{{.State.Health.Status}}' kb-mcp` | `healthy` |
| A8 | `docker exec kb-mcp id -un` | `app` (not root) |

### B. Image smoke test (starts its own throwaway containers)

```bash
docker rm -f kb-mcp                      # frees port 8000
scripts/smoke_image.sh kb-mcp
```

Pass: four `ok:` lines (missing env vars fail fast, `/health` returns ok, `/mcp` without a token returns 401, `docker stop` exits with code 0), then `smoke test passed`.

Restart the container from step 2 before part C.

### C. Real GitHub connection (needs real `.env` values)

| # | Command | Expected |
|---|---|---|
| C1 | `scripts/mcp_call.sh get_context` | `# Root index (INDEX.md)` followed by the root index text |
| C2 | `scripts/mcp_call.sh list_dir` | `Contents of (top level):` and one entry per line, folders end with `/` |
| C3 | `scripts/mcp_call.sh read_file '{"path":"INDEX.md","max_chars":200}'` | `# INDEX.md (characters 0–200 of N)` and a `[continues — call read_file ... offset=200]` footer |
| C4 | `scripts/mcp_call.sh search '{"query":"<a word you know is in the KB>"}'` | `N documents match ...` with paths and `line N:` snippets |
| C5 | `scripts/mcp_call.sh read_file '{"path":"no/such/file.md"}'` | `Not found in the knowledge base: no/such/file.md. Use list_dir or search ...` |
| C6 | `scripts/mcp_call.sh list_dir '{"path":"INDEX.md"}'` | `INDEX.md is a file. Use read_file to read it.` |

If C1 returns `The knowledge base is temporarily unavailable (GitHub refused or rate-limited the request)`, the PAT is wrong or expired, or `KB_REPO` is wrong. Check `docker logs kb-mcp`. A GitHub 401 or 404 is logged there, never the token or file contents.

### D. Logging (read `docker logs kb-mcp` after each step)

Needs only dummy credentials, except D6 and D7.

| # | Do | Expected in the logs |
|---|---|---|
| D1 | `docker logs kb-mcp` right after start | `Starting kb-mcp version=… repo=… branch=… bearer_tokens=1 … log_level=INFO`, then `Startup check failed` preceded by `GitHub GET …/branches/main -> 401 … GITHUB_TOKEN is invalid or expired` (dummy token). With a real token: `Startup check ok: head=<sha> root_index=INDEX.md (N chars)` |
| D2 | `curl -s localhost:8000/health` three times | No new `/health` lines |
| D3 | `curl -s -X POST localhost:8000/mcp` | `WARNING … Rejected POST '/mcp': no Authorization header client=… user_agent='curl/…'` |
| D4 | `curl -s -X POST localhost:8000/mcp -H 'Authorization: Bearer wrong'` | `Rejected … token matches none of the configured tokens (presented 5 chars, Bearer form)`. The word `wrong` is not logged as a token value |
| D5 | `scripts/mcp_call.sh search '{"query":"  "}'` | `WARNING … tool=search query='  ' -> KBError in 0 ms, 0 chars` |
| D6 | Real creds: `scripts/mcp_call.sh get_context` | `GitHub GET /repos/…/contents/INDEX.md -> 200 in N ms (rate limit remaining 49xx)`, then `tool=get_context -> ok in N ms, M chars` |
| D7 | Real creds: `scripts/mcp_call.sh search '{"query":"index"}'` | `GitHub GET …/tarball/main -> 200`, `GitHub tarball: X MB downloaded, N text files …`, then `tool=search query='index' -> ok …` |
| D8 | Restart with `-e LOG_LEVEL=warning` | No `INFO` lines at all; only warnings and errors |
| D9 | Restart with `-e LOG_LEVEL=banana` | `Ignoring invalid LOG_LEVEL='BANANA'; using INFO` |
| D10 | `docker run --rm kb-mcp` (no env) | One line `CRITICAL … Startup failed: Missing required environment variable GITHUB_TOKEN`, exit code 1, no traceback |

Pass: every row shows the expected lines. No token, file content or `codeload.github.com` URL appears anywhere in the logs.

### Reading the logs

Every line is `<UTC time> <LEVEL> <logger>: <message>`.

| Log line | Meaning | Fix |
|---|---|---|
| `Starting kb-mcp version=… commit=…` | The process booted. It prints the config in effect (token count, never values). `commit` is the Render deploy. | If `repo`, `branch` or `root_index` are wrong, fix the env vars. |
| `CRITICAL … Startup failed: Missing required environment variable X` | The config is missing or invalid, and the process exits. | Set the variable. |
| `Startup check ok: head=abc1234 …` | Token, repo, branch and root index all work. `head` is the KB commit being served. | None. |
| `GitHub GET … -> 401 … GITHUB_TOKEN is invalid or expired` | GitHub rejected the token. | Create a new fine-grained PAT. |
| `GitHub GET … -> 403 … lacks Contents read access … or rate limit is exhausted` | The PAT can't read the repo, or the rate limit is used up. Check `rate limit remaining`. | Give the PAT Contents read-only on the KB repo, or wait for the reset time. |
| `Startup check failed: The knowledge base repository or branch is not reachable` | GitHub answered 404. `KB_REPO` or `KB_BRANCH` is wrong, or the PAT can't see a private repo. | Fix the env vars or the PAT's repository access. |
| `Startup check failed: root index 'INDEX.md' not found …` | `KB_ROOT_INDEX` is wrong, or the KB has no such file. | Fix the variable or add the file. |
| `Rejected POST '/mcp': no Authorization header` | A request without a token. Either the connector has no header, or it's an internet scanner. | In the Claude connector, add header `authorization`. |
| `Rejected …: token matches none of the configured tokens (presented N chars …)` | A wrong token. Tokens from `token_urlsafe(48)` are 64 chars, so a different N means it was truncated or mis-pasted. | Remove the connector and add it again. |
| `tool=read_file path='x' -> KBNotFoundError …` (WARNING) | Claude asked for a path that doesn't exist. Normal. | None. |
| `tool=… -> crashed with KeyError` (ERROR), plus a traceback from `mcp.server.mcpserver.server` | A bug in the server. | Open an issue with the traceback. |
| `GitHub rate limit low: 42 requests left, resets at 14:05:00Z` | Close to GitHub's hourly limit. | Wait for the reset time. |
| `GitHub GET … failed after 15000 ms: ReadTimeout(…)` | GitHub is slow or unreachable. | Retry. |
| `GitHub tarball: 40.2 MB downloaded …` then the log ends and `Starting kb-mcp` appears again | `search` downloads the whole repo in memory. On the 512 MB free tier a big KB can exhaust RAM. | Check Render's events for an out-of-memory restart. |
| `Starting kb-mcp` appears again with no error before it | The instance restarted: it woke after sleeping, or the process crashed. | Check Render's events. |

## 4. Clean up

```bash
docker stop kb-mcp
docker rmi kb-mcp        # optional
```

## Pass / fail

Pass: A1–A8, B and D1–D5, D8–D10 pass with any credentials. C1–C6, D6 and D7 also pass with real credentials.

Fail: any expected output above differs. Collect `docker logs kb-mcp` before stopping the container.

## Notes

- The image defaults to `PORT=8000`. To change the port, set it on both sides: `-e PORT=9000 -p 9000:9000`. Then set `MCP_URL=http://localhost:9000/mcp` for `mcp_call.sh`.
- To test the Render-style cold start, stop the container, start it again and poll `/health` until it returns `ok`.
- CI runs the same smoke test (`scripts/smoke_image.sh`) on every PR, so part B is what CI checks.
