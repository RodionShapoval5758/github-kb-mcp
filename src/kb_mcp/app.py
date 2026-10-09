import logging

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .auth import BearerAuth
from .config import load_settings
from .github_reader import GitHubReader
from .tools import register_tools

INSTRUCTIONS = (
    "Read-only access to a project knowledge base: meeting transcripts, architecture decisions, findings, "
    "proposed designs and requirements. Call get_context first in every conversation: the root index explains "
    "the project, the current focus and where each kind of document lives. Follow index pointers with read_file "
    "and list_dir; use search when the indexes don't point to what you need. Conversations are often spoken, "
    "so prefer few, targeted tool calls."
)

logging.basicConfig(level=logging.INFO)
# httpx logs every request URL at INFO; the tarball redirect URL carries a temporary download token.
logging.getLogger("httpx").setLevel(logging.WARNING)

settings = load_settings()
server = MCPServer(name="kb-mcp", instructions=INSTRUCTIONS)
register_tools(
    server,
    GitHubReader(settings.kb_repo, settings.kb_branch, settings.github_token),
    settings.kb_root_index,
)


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


# host="0.0.0.0" disables the SDK's localhost-only Host-header check, which would reject the Render hostname.
app = BearerAuth(
    server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0"),
    tokens=settings.bearer_tokens,
)
