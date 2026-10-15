import hmac
import logging
from collections.abc import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)

MAX_LOGGED_CHARS = 100


class BearerAuth:
    def __init__(self, app: ASGIApp, tokens: Sequence[str], protected_prefix: str = "/mcp"):
        self._app = app
        self._tokens = [t.encode() for t in tokens]
        self._prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self._prefix):
            headers = dict(scope["headers"])
            reason = self._rejection(headers)
            if reason:
                log.warning(
                    "Rejected %s %r: %s client=%r user_agent=%r",
                    scope["method"],
                    scope["path"][:MAX_LOGGED_CHARS],
                    reason,
                    self._client(scope, headers),
                    headers.get(b"user-agent", b"").decode("latin-1")[:MAX_LOGGED_CHARS],
                )
                # No WWW-Authenticate OAuth metadata: Claude's connector dialog would otherwise try an OAuth flow.
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)

    def _rejection(self, headers: dict[bytes, bytes]) -> str | None:
        header = headers.get(b"authorization", b"").strip()
        if not header:
            return "no Authorization header"
        scheme, _, rest = header.partition(b" ")
        bearer = scheme.lower() == b"bearer"
        # Claude sends the header value exactly as typed, so accept a bare token as well as "Bearer <token>".
        token = rest.strip() if bearer else header
        if not token:
            return "Authorization header has no token after 'Bearer'"
        if any(hmac.compare_digest(token, t) for t in self._tokens):
            return None
        form = "Bearer" if bearer else "bare"
        return f"token matches none of the configured tokens (presented {len(token)} chars, {form} form)"

    @staticmethod
    def _client(scope: Scope, headers: dict[bytes, bytes]) -> str:
        forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1").split(",")[0].strip()
        if forwarded:
            return forwarded[:45]
        client = scope.get("client")
        return client[0] if client else "unknown"
