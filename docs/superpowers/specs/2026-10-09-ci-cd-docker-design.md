# Docker image, CI and CD for kb-mcp

Date: 2026-10-09

## Goal

Build a container image of the KB MCP server, check every change in GitHub Actions, and publish the image to Docker Hub when `main` or a release tag is pushed. Then report whether the app is ready to deploy.

## Decisions

| Topic | Decision |
|---|---|
| Registry | Docker Hub, `<DOCKERHUB_USERNAME>/kb-mcp` |
| What CD does | Build and push the image only. No deploy step. |
| Render | `render.yaml` is unchanged. Render keeps deploying from git. The Docker Hub image is a separate artifact. |
| Platforms | `linux/amd64` only |
| Base image | `python:3.12-slim` (the `requires-python` floor) |
| Dockerfile name | `Dockerfile` (single service) |
| Tests | None added. The repo has none and none are requested. |

## Files

### `Dockerfile`

- Builder stage: copy `uv` from its official image (pinned version), run `uv sync --frozen --no-dev --no-install-project` against `pyproject.toml` + `uv.lock` for a cacheable dependency layer, then copy `src/` and run `uv sync --frozen --no-dev --no-editable`.
- Final stage: `python:3.12-slim`, copy only `/app/.venv`, create and switch to a non-root user.
- `ENV PORT=8000`, `EXPOSE 8000`.
- `HEALTHCHECK` calls `GET /health` with Python's `urllib` (no curl in the slim image).
- `CMD` runs `uvicorn kb_mcp.app:app --host 0.0.0.0 --port ${PORT:-8000}` through `sh -c` with `exec`, so `$PORT` expands and uvicorn still receives signals.

### `.dockerignore`

`.env`, `.venv`, `.git`, `.github`, `__pycache__`, `*.egg-info`, `dist`, `docs`, `scripts`, `*.md`. The build needs only `pyproject.toml`, `uv.lock` and `src/` (`pyproject.toml` declares no `readme`).

### `.github/workflows/ci.yml`

Triggers: `pull_request`, `push` to `main`, `workflow_call`. Permissions: `contents: read`. No concurrency block, so CD's call into this workflow is never cancelled.

Job `checks`:
1. `actions/checkout`
2. `astral-sh/setup-uv` (cache on)
3. `uv sync --frozen`
4. `uv lock --check`
5. `uv run ruff check`

`ruff format --check` is not run: it fails on the existing `src/` at every line length (slice spacing, joined strings), and `src/` is not reformatted.

Job `docker`:
1. `actions/checkout`
2. `docker/setup-buildx-action`
3. `docker/build-push-action` with `push: false`, `load: true`, GitHub Actions cache.
4. `bash scripts/smoke_image.sh kb-mcp:ci` (see below).

### `scripts/smoke_image.sh <image>`

Used by the CI `docker` job and by the local readiness check. Steps, each printing `ok: ...` or `FAIL: ...`:
1. Run the image with no env vars: it must exit non-zero and print `Missing required environment variable`.
2. Run the image detached with dummy `GITHUB_TOKEN`, `KB_REPO=owner/repo`, an `MCP_BEARER_TOKENS` value of at least 32 characters, and `PORT=8080` (proves the container honors `$PORT`).
3. Poll `GET /health` until it returns `ok` (fail after about 30 s).
4. Assert `POST /mcp` without a token returns `401`.
5. `docker stop`: the exit code must not be `137` (SIGTERM reaches uvicorn).
6. Always remove the container; print its logs on failure.

### `.github/workflows/cd.yml`

Triggers: `push` to `main` and tags matching `v*`. Permissions: `contents: read`.

- Job `ci`: `uses: ./.github/workflows/ci.yml`.
- Job `publish` (`needs: ci`):
  1. `actions/checkout`
  2. `docker/setup-buildx-action`
  3. `docker/login-action` with `secrets.DOCKERHUB_USERNAME` and `secrets.DOCKERHUB_TOKEN`
  4. `docker/metadata-action` with image `<DOCKERHUB_USERNAME>/kb-mcp`. Tags: `latest` and the short SHA on `main`; semver (`1.2.3`, `1.2`) on `v*` tags.
  5. `docker/build-push-action` with `push: true`, the metadata tags and labels, GitHub Actions cache, `linux/amd64`.

Required repo secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` (Docker Hub access token, Read & Write).

### `pyproject.toml` and `uv.lock`

- Add `[dependency-groups] dev = ["ruff"]`.
- Add `[tool.ruff]` with `target-version = "py312"`. Default rules only; `ruff check` already passes on `src/`.
- `uv.lock` is regenerated to include `ruff`.

### `README.md`

New section "Docker and CI/CD": local `docker build` and `docker run --env-file .env -p 8000:8000`, the two secrets, and the tagging rules.

## Readiness check (after implementation)

Run locally and report the real output:
1. `docker build` succeeds.
2. `scripts/smoke_image.sh` passes: missing env fails fast, `/health` returns `ok` on a custom `PORT`, `/mcp` without a token returns `401`, `docker stop` is not a SIGKILL.
3. The image contains no `.env` file and runs as a non-root user.
4. The `HEALTHCHECK` reports `healthy`.
5. `uv lock --check` and `ruff check` pass; `actionlint` accepts both workflows.

Manual steps for the user (not done by Claude): commit, create the GitHub repo and push, add the two Docker Hub secrets, watch the first Actions run.

## Out of scope

- Deploying the image anywhere (Render image runtime, SSH to a host).
- Multi-arch builds.
- Image vulnerability scanning and signing.
- Writing tests.
- Committing any files.

## Risks

- `ruff format --check` fails on the existing code at every line length (checked with ruff 0.16.10). Resolved by not running it in CI.
- `docker` job needs the Docker daemon on the runner. `ubuntu-latest` provides it.
- The repo has no commits and no remote, so no workflow can run until the user pushes.
