#!/usr/bin/env bash
set -euo pipefail

: "${MCP_URL:=http://localhost:8000/mcp}"
: "${MCP_TOKEN:?Set MCP_TOKEN to one of the MCP_BEARER_TOKENS values}"

tool="${1:?Usage: scripts/mcp_call.sh <tool> [json-arguments]}"
arguments="${2:-}"
[ -n "$arguments" ] || arguments='{}'

curl -sS -X POST "$MCP_URL" \
  -H "Authorization: Bearer $MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$tool\",\"arguments\":$arguments}}" \
  | python3 -c '
import json, sys
response = json.load(sys.stdin)
if "error" in response:
    sys.exit("protocol error: " + json.dumps(response["error"]))
result = response["result"]
print("".join(c.get("text", "") for c in result["content"]))
if result.get("isError"):
    sys.exit(1)
'
