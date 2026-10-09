import hmac
from collections.abc import Sequence

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerAuth:
    def __init__(self, app: ASGIApp, tokens: Sequence[str], protected_prefix: str = "/mcp"):
        self._app = app
        self._tokens = [t.encode() for t in tokens]
        self._prefix = protected_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(self._prefix) and not self._authorized(scope):
            # No WWW-Authenticate OAuth metadata: Claude's connector dialog would otherwise try an OAuth flow.
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _authorized(self, scope: Scope) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"").strip()
        scheme, _, rest = header.partition(b" ")
        # Claude sends the header value exactly as typed, so accept a bare token as well as "Bearer <token>".
        token = rest.strip() if scheme.lower() == b"bearer" else header
        return bool(token) and any(hmac.compare_digest(token, t) for t in self._tokens)
