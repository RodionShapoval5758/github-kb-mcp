#!/usr/bin/env bash
set -euo pipefail

image="${1:?Usage: scripts/smoke_image.sh <image>}"
host_port="${SMOKE_PORT:-8000}"
container_port=8080
name="kb-mcp-smoke-$$"
noenv_name="$name-noenv"

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$name" 2>&1 | tail -n 30 || true
  fi
  docker rm -f "$name" "$noenv_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --name "$noenv_name" "$image" >/dev/null
if ! status=$(timeout 30 docker wait "$noenv_name"); then
  echo "FAIL: container still running 30 s after start without the required env vars"
  exit 1
fi
if [ "$status" -eq 0 ]; then
  echo "FAIL: container started without the required env vars"
  exit 1
fi
output=$(docker logs "$noenv_name" 2>&1)
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
