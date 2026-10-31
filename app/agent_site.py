"""Single-container local agent: bundled UI + authenticated /api routes.

The installer publishes only 127.0.0.1. Host validation also prevents a
DNS-rebinding origin from reading the local UI/API through an arbitrary host.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse, HTMLResponse
from starlette.staticfiles import StaticFiles

from app import agent
from app.main import app as api


class ApiPrefix:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        # Mount retains the original path. AuthMiddleware needs the API path.
        scope = dict(scope)
        path = scope.get("path", "")
        if path.startswith("/api/"):
            scope["path"] = path[4:]
            scope["raw_path"] = scope["path"].encode("utf-8")
            scope["root_path"] = ""
        await self.wrapped(scope, receive, send)


@asynccontextmanager
async def lifespan(_app):
    if not agent.enabled():
        raise RuntimeError("Local inventory agent configuration is required.")
    # Read business records and update incident metadata only; never AI/apply.
    agent.scan(register=True)
    yield


def create_site(frontend=None):
    directory = Path(frontend or "/opt/afterfix/frontend")
    async def health(_request):
        return JSONResponse({"ok": True})
    async def index(_request):
        html = (directory / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("</head>", '<meta name="afterfix-workspace" content="connected" /></head>'), headers={"Cache-Control": "no-store"})
    site = Starlette(routes=[Route("/health", health), Route("/", index), Mount("/api", app=ApiPrefix(api)), Mount("/", app=StaticFiles(directory=directory, html=True, check_dir=False))], lifespan=lifespan)
    site.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    return site


app = create_site()
