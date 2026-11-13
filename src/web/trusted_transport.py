"""Process-local transport evidence supplied by the managed Unix listener host."""

from starlette.types import ASGIApp, Receive, Scope, Send

_PRIVATE_UNIX_SCOPE_KEY = "vortoone.private_unix_transport"
_PRIVATE_UNIX_SCOPE_MARKER = object()


def is_private_unix_transport(scope: Scope) -> bool:
    """Headers, query parameters and missing peer addresses cannot supply this evidence."""
    return scope.get(_PRIVATE_UNIX_SCOPE_KEY) is _PRIVATE_UNIX_SCOPE_MARKER


class PrivateUnixTransport:
    """Wrap only the app instance passed to the trusted host's private UDS listener.

    The host validates the socket directory and permissions before starting the
    listener. Do not install this wrapper on a network listener or the shared app.
    Authentication and Origin checks remain the responsibility of the real routes.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope = {**scope, _PRIVATE_UNIX_SCOPE_KEY: _PRIVATE_UNIX_SCOPE_MARKER}
        await self.app(scope, receive, send)
