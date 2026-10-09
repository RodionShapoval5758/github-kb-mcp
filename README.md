# kb-mcp

Read-only MCP server that lets Claude (web, mobile, voice) read one GitHub-hosted project knowledge base. See `CLAUDE.md` for how it works.

## 1. Tokens

- **GitHub:** fine-grained PAT, repository access = only the KB repo, permissions = Contents: Read-only.
- **Connector bearer token:** `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Several are allowed, comma-separated in `MCP_BEARER_TOKENS` (e.g. one per person).

## 2. Run locally

```bash
cp .env.example .env   # fill in values
uv sync
uv run --env-file .env uvicorn kb_mcp.app:app --port 8000
MCP_TOKEN=<bearer token> scripts/mcp_call.sh get_context
```

## 3. Deploy on Render

1. Push this repo to GitHub.
2. Render → **New** → **Blueprint** → pick the repo. Render reads `render.yaml`.
3. Enter `GITHUB_TOKEN`, `KB_REPO` (`owner/name`) and `MCP_BEARER_TOKENS` when prompted.
4. Wait for the deploy, then open `https://<service>.onrender.com/health` and check that it shows `ok`.

Pushing to the default branch redeploys automatically.

## 4. Connect Claude

1. Claude → **Customize → Connectors → Add custom connector**.
2. Name: `KB`. URL: `https://<service>.onrender.com/mcp`. Then **Continue**.
3. The check reports *Couldn't check the server*. That's expected, because there's no OAuth. Choose **Continue anyway**.
4. Authentication: **No sign-in**. **Request headers**: name `authorization`, value `Bearer <bearer token>` (include `Bearer ` and the space). Mark it required. **Add**.
5. In the connector's tool list, set the tools to **Always allow**, so voice sessions aren't interrupted by permission prompts.

Header values can't be edited. To rotate the token, remove the connector and add it again.

## 5. Before a voice session

The free Render instance sleeps after ~15 minutes idle and takes 30–60 s to wake. Open `https://<service>.onrender.com/health` first and wait for `ok`.

## 6. Docker and CI/CD

Build and run the image locally:

```bash
docker build -t kb-mcp .
docker run --rm --env-file .env -p 8000:8000 kb-mcp
```

CI (`.github/workflows/ci.yml`) runs on every pull request and push to `main`: lockfile check, `ruff check`, an image build and a smoke test (`scripts/smoke_image.sh`).

CD (`.github/workflows/cd.yml`) runs on pushes to `main` and on `v*` tags. After CI passes it pushes `<DOCKERHUB_USERNAME>/kb-mcp` to Docker Hub:

| Trigger | Tags |
|---|---|
| push to `main` | `latest`, `sha-<short sha>` |
| tag `v1.2.3` | `1.2.3`, `1.2` |

Add two repository secrets (Settings → Secrets and variables → Actions):

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`: a Docker Hub access token with Read & Write scope

CD only publishes the image. Render keeps deploying from git (`render.yaml`).
