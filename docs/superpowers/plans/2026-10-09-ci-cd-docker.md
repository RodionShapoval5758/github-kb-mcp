# Docker, CI and CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a container image of kb-mcp, check every change in GitHub Actions, and push the image to Docker Hub from `main` and `v*` tags.

**Architecture:** A multi-stage `Dockerfile` (uv builds the venv from `uv.lock`, a slim non-root runtime stage copies it). `scripts/smoke_image.sh` runs the built image and is shared by CI and the local readiness check. `ci.yml` runs lint, lockfile and Docker checks; `cd.yml` calls `ci.yml` and then pushes to Docker Hub.

**Tech Stack:** Docker (buildx), uv 0.12.15, ruff, GitHub Actions, Docker Hub.

**Spec:** `docs/superpowers/specs/2026-10-09-ci-cd-docker-design.md`

## Global Constraints

- Registry is Docker Hub, image `<DOCKERHUB_USERNAME>/kb-mcp`. CD builds and pushes only; no deploy step.
- `render.yaml` is not touched.
- Platform `linux/amd64` only. Base image `python:3.12-slim`.
- The file is named `Dockerfile` (single service).
- Do not write or modify tests. `scripts/smoke_image.sh` is a CI smoke check, not a test suite.
- Do not run `git commit`, `git add` or `git push`. The user commits.
- Do not reformat or edit anything under `src/`.
- Code and config files carry no comments unless the WHY is non-obvious.
- Address the user as "dude" in messages.
- Required repo secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` (Docker Hub access token, Read & Write).
- Tags: `latest` and `sha-<short sha>` on `main`; `1.2.3` and `1.2` on `v*` tags.

## Review Focus

- Render injects `PORT` (e.g. 10000), not 8000: the container must bind to `$PORT`. Pinned by Task 1 (the smoke script runs the container with `PORT=8080`).
- `docker stop` must reach uvicorn as SIGTERM, not wait for a SIGKILL (exit 137). Pinned by Task 1 (smoke script).
- Missing or invalid env vars must make the container exit non-zero with the `ConfigError` text instead of hanging. Pinned by Task 1 (smoke script).
- A stray `.env` or running as root must not end up in the image. Pinned by Task 1 (image inspection steps).
- Adding `ruff` makes `uv.lock` drift, and CI uses `--frozen`: a stale lockfile must fail CI, not silently pass. Pinned by Task 2 (`uv lock --check`).

---

### Task 1: Docker image and smoke script

**Files:**
- Create: `.dockerignore`
- Create: `Dockerfile`
- Create: `scripts/smoke_image.sh`

**Interfaces:**
- Consumes: `pyproject.toml`, `uv.lock`, `src/kb_mcp/` (entry point `kb_mcp.app:app`, `GET /health` returns `ok`, `/mcp` returns 401 without a token, env vars `GITHUB_TOKEN`, `KB_REPO`, `MCP_BEARER_TOKENS`).
- Produces: an image runnable with `docker run -e GITHUB_TOKEN=... -e KB_REPO=... -e MCP_BEARER_TOKENS=... -e PORT=<n> -p <host>:<n> <image>`; `bash scripts/smoke_image.sh <image>` exits 0 on success. Tasks 2 and 3 call the script with `kb-mcp:ci`.

- [ ] **Step 1: Confirm Docker works**

Run: `docker info --format '{{.ServerVersion}}'`
Expected: a version number (e.g. `29.x`). If it errors, tell dude the Docker daemon is not running and stop.

- [ ] **Step 2: Create `.dockerignore`**

```
**/.env
**/__pycache__
**/*.egg-info
.venv
.git
.github
dist
docs
scripts
*.md
```

- [ ] **Step 3: Create `Dockerfile`**

```dockerfile
FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --create-home --uid 10001 app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PORT=8000
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ['PORT'], timeout=2)"
CMD ["sh", "-c", "exec uvicorn kb_mcp.app:app --host 0.0.0.0 --port ${PORT}"]
```

- [ ] **Step 4: Create `scripts/smoke_image.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

image="${1:?Usage: scripts/smoke_image.sh <image>}"
host_port="${SMOKE_PORT:-8000}"
container_port=8080
name="kb-mcp-smoke-$$"

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$name" 2>&1 | tail -n 30 || true
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if output=$(docker run --rm "$image" 2>&1); then
  echo "FAIL: container started without the required env vars"
  exit 1
fi
if ! grep -q "Missing required environment variable" <<<"$output"; then
  echo "FAIL: container failed without the expected ConfigError message:"
  echo "$output"
  exit 1
fi
echo "ok: missing env vars fail fast"

docker run -d --name "$name" -p "$host_port:$container_port" \
  -e PORT="$container_port" \
  -e GITHUB_TOKEN=dummy \
  -e KB_REPO=owner/repo \
  -e MCP_BEARER_TOKENS=smoke-test-token-smoke-test-token-smoke-test-token \
  "$image" >/dev/null

health_url="http://localhost:$host_port/health"
for _ in $(seq 1 30); do
  if [ "$(curl -s "$health_url" || true)" = "ok" ]; then
    break
  fi
  sleep 1
done
if [ "$(curl -s "$health_url" || true)" != "ok" ]; then
  echo "FAIL: $health_url did not return ok within 30 s"
  exit 1
fi
echo "ok: /health returns ok on PORT=$container_port"

code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:$host_port/mcp")
if [ "$code" != "401" ]; then
  echo "FAIL: POST /mcp without a token returned $code, expected 401"
  exit 1
fi
echo "ok: /mcp without a token returns 401"

docker stop -t 10 "$name" >/dev/null
exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$name")
if [ "$exit_code" = "137" ]; then
  echo "FAIL: container had to be killed (exit 137); SIGTERM is not reaching uvicorn"
  exit 1
fi
echo "ok: docker stop exited with code $exit_code"

echo "smoke test passed"
```

- [ ] **Step 5: Make the script executable**

Run: `chmod +x scripts/smoke_image.sh`
Expected: no output.

- [ ] **Step 6: Build the image**

Run: `docker build -t kb-mcp:local .`
Expected: build succeeds; last lines include `naming to docker.io/library/kb-mcp:local`. If it fails on `ghcr.io/astral-sh/uv:0.12.15` not found, stop and tell dude (the tag must match the installed uv, `uv --version`).

- [ ] **Step 7: Run the smoke script**

Run: `bash scripts/smoke_image.sh kb-mcp:local`
Expected output, in order:
```
ok: missing env vars fail fast
ok: /health returns ok on PORT=8080
ok: /mcp without a token returns 401
ok: docker stop exited with code 0
smoke test passed
```
Pass: exit code 0. Fail: any `FAIL:` line; read the printed container logs, fix the Dockerfile or script, rerun.

- [ ] **Step 8: Inspect the image for leaks and root**

Run: `docker run --rm --entrypoint sh kb-mcp:local -c 'id -u; find / -xdev -name ".env" 2>/dev/null'`
Expected: first line `10001`, then no further output (no `.env` anywhere).

- [ ] **Step 9: Check the HEALTHCHECK**

Run:
```bash
docker run -d --name kb-mcp-health -e GITHUB_TOKEN=dummy -e KB_REPO=owner/repo \
  -e MCP_BEARER_TOKENS=smoke-test-token-smoke-test-token-smoke-test-token kb-mcp:local
sleep 45
docker inspect -f '{{.State.Health.Status}}' kb-mcp-health
docker rm -f kb-mcp-health
```
Expected: `healthy`. If `starting` or `unhealthy`, run `docker inspect -f '{{json .State.Health}}' kb-mcp-health` before removing the container and fix the `HEALTHCHECK` line.

---

### Task 2: Ruff dev dependency and CI workflow

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock` (regenerated by `uv lock`)
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `scripts/smoke_image.sh <image>` from Task 1 (exit 0 = pass).
- Produces: a reusable workflow `./.github/workflows/ci.yml` (trigger `workflow_call`) with jobs `checks` and `docker`. Task 3 calls it with `uses: ./.github/workflows/ci.yml`.

- [ ] **Step 1: Add ruff to `pyproject.toml`**

Append to the end of the file:

```toml

[dependency-groups]
dev = ["ruff>=0.16"]

[tool.ruff]
target-version = "py312"
```

- [ ] **Step 2: Regenerate the lockfile**

Run: `uv lock`
Expected: output mentions `Added ruff v0.16.x` and no other change. If it lists updated packages, stop and tell dude, because an unrelated bump would end up in the image.

- [ ] **Step 3: Verify lockfile and lint**

Run: `uv lock --check && uv sync --frozen && uv run ruff check`
Expected: `Resolved ... packages`, then `All checks passed!`.

- [ ] **Step 4: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]
  workflow_call:

permissions:
  contents: read

jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
          enable-cache: true
      - run: uv lock --check
      - run: uv sync --frozen
      - run: uv run ruff check

  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: kb-mcp:ci
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - run: bash scripts/smoke_image.sh kb-mcp:ci
```

- [ ] **Step 5: Lint the workflow**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.7 -color .github/workflows/ci.yml`
Expected: no output, exit code 0.

- [ ] **Step 6: Rebuild the image against the new lockfile**

Run: `docker build -t kb-mcp:local . && bash scripts/smoke_image.sh kb-mcp:local`
Expected: build succeeds and the smoke test prints `smoke test passed`. (Confirms `--no-dev` keeps ruff out of the image and `--frozen` accepts the regenerated lock.)

---

### Task 3: CD workflow and README

**Files:**
- Create: `.github/workflows/cd.yml`
- Modify: `README.md` (append section 6)

**Interfaces:**
- Consumes: `./.github/workflows/ci.yml` from Task 2 (callable workflow). Repo secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.
- Produces: Docker Hub tags as listed in Global Constraints.

- [ ] **Step 1: Create `.github/workflows/cd.yml`**

```yaml
name: CD

on:
  push:
    branches: [main]
    tags: ["v*"]

permissions:
  contents: read

jobs:
  ci:
    uses: ./.github/workflows/ci.yml

  publish:
    needs: ci
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/kb-mcp
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          platforms: linux/amd64
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Lint both workflows**

Run: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.7 -color .github/workflows/ci.yml .github/workflows/cd.yml`
Expected: no output, exit code 0.

- [ ] **Step 3: Append the README section**

Append to the end of `README.md`:

````markdown

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
````

---

### Task 4: Readiness check and handoff

**Files:**
- No file changes.

**Interfaces:**
- Consumes: everything from Tasks 1 to 3.
- Produces: a readiness report and a manual verification guide for dude.

- [ ] **Step 1: Run every check from a clean state**

Run:
```bash
docker build --no-cache -t kb-mcp:local .
bash scripts/smoke_image.sh kb-mcp:local
uv lock --check
uv run ruff check
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.7 -color
docker image ls kb-mcp:local --format '{{.Size}}'
```
Expected: build succeeds; `smoke test passed`; `Resolved ... packages`; `All checks passed!`; actionlint prints nothing; an image size.

- [ ] **Step 2: Confirm nothing outside scope changed**

Run: `git status --short`
Expected: `Dockerfile`, `.dockerignore` and `.github/` appear as untracked next to the existing untracked entries, and no commit exists. No task edits `render.yaml` or `src/`.

- [ ] **Step 3: Report to dude**

Send one message with:
1. A readiness verdict: ready or not, with the real output of Step 1 for anything that failed.
2. The deviations from the spec: no `ruff format --check` (fails on existing `src/`); extra file `scripts/smoke_image.sh`.
3. Things only dude can do, as a manual guide:
   - Commit the files (nothing is committed).
   - Create the GitHub repo, add it as `origin`, push `main`.
   - Create a Docker Hub access token (Read & Write) and add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` as repo secrets.
   - Watch the first Actions run: pass means CI green, then CD pushes `latest` and `sha-<short sha>`.
   - Confirm with `docker pull <DOCKERHUB_USERNAME>/kb-mcp:latest` and the `docker run` line from the README, then `curl localhost:8000/health` returns `ok`.
   - Pass/fail: CI and CD workflows are green, and the pulled image answers `/health` with `ok`.
4. Known limits: Render still deploys from git, not from this image; the free Render tier sleeps after about 15 minutes idle.
