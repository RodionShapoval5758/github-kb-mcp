import asyncio
import logging
import os
import platform
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version

from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from .auth import BearerAuth
from .config import ConfigError, load_settings
from .errors import KBError, KBNotFoundError
from .github_reader import GitHubReader
from .logging_setup import configure_logging
from .tools import register_tools

INSTRUCTIONS = (
    "Read-only access to a project knowledge base: meeting transcripts, architecture decisions, findings, "
    "proposed designs and requirements. Call get_context first in every conversation: the root index explains "
    "the project, the current focus and where each kind of document lives. Follow index pointers with read_file "
    "and list_dir; use search when the indexes don't point to what you need. Conversations are often spoken, "
    "so prefer few, targeted tool calls."
)

configure_logging()
log = logging.getLogger(__name__)


def _package_version() -> str:
    try:
        return version("kb-mcp")
    except PackageNotFoundError:
        return "unknown"


try:
    settings = load_settings()
except ConfigError as exc:
    log.critical("Startup failed: %s", exc)
    raise SystemExit(1) from exc

log.info(
    "Starting kb-mcp version=%s commit=%s python=%s repo=%s branch=%s root_index=%s bearer_tokens=%d port=%s log_level=%s",
    _package_version(),
    os.environ.get("RENDER_GIT_COMMIT", "unknown")[:7],
    platform.python_version(),
    settings.kb_repo,
    settings.kb_branch,
    settings.kb_root_index,
    len(settings.bearer_tokens),
    os.environ.get("PORT", "unset"),
    logging.getLevelName(logging.getLogger().level),
)

reader = GitHubReader(settings.kb_repo, settings.kb_branch, settings.github_token)
server = MCPServer(name="kb-mcp", instructions=INSTRUCTIONS)
register_tools(server, reader, settings.kb_root_index)


@server.custom_route("/health", methods=["GET"])
async def health(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def check_github() -> None:
    try:
        head, size = await reader.check_access(settings.kb_root_index)
    except KBNotFoundError:
        log.error(
            "Startup check failed: root index %r not found on branch %r of %s. Check KB_ROOT_INDEX.",
            settings.kb_root_index,
            settings.kb_branch,
            settings.kb_repo,
        )
    except KBError as exc:
        log.error("Startup check failed: %s", exc)
    except Exception:
        log.exception("Startup check crashed")
    else:
        log.info("Startup check ok: head=%s root_index=%s (%d chars)", head, settings.kb_root_index, size)


# host="0.0.0.0" disables the SDK's localhost-only Host-header check, which would reject the Render hostname.
mcp_app = server.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
sdk_lifespan = mcp_app.router.lifespan_context


@asynccontextmanager
async def lifespan(app: Starlette):
    # Background and non-fatal: a bad token must not keep /health from answering (the smoke test uses a dummy one).
    check = asyncio.create_task(check_github())
    try:
        async with sdk_lifespan(app) as state:
            yield state
    finally:
        check.cancel()
        log.info("Shutting down")


mcp_app.router.lifespan_context = lifespan
app = BearerAuth(mcp_app, tokens=settings.bearer_tokens)
