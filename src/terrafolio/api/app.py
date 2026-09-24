"""``create_app()`` — the factory, and the order the routes have to be mounted in.

A factory rather than a module-level ``app``: a test builds several applications
in one process, each against its own pipeline directory and its own database, and
a global would make them share state through the back door.

The web pages are mounted **last**, so every API path is matched before the
static handler sees it. Mounting them first would shadow the whole API with a
404 from a directory that has no such file.

Only the four pages and the five asset directories are served. Mounting
``web/`` wholesale also published ``package.json``, ``tailwind.config.js``, the
un-compiled ``src/``, the node test suite and ``node_modules/`` — and nothing in
this backlog authenticates (epic §12 Q7) while §12 calls the data commercially
sensitive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import terrafolio
from terrafolio.api.errors import ApiError, ErrorCode, install_error_handlers
from terrafolio.api.routes_pipeline import router as pipeline_router
from terrafolio.api.routes_runs import router as runs_router
from terrafolio.api.service import Service, build_service
from terrafolio.api.settings import Settings

__all__ = ["ASSET_DIRECTORIES", "PAGES", "create_app"]

PAGES: Final[tuple[str, ...]] = ("mandate", "search", "portfolio", "styleguide")
"""The four screens 1D ships. ``mandate`` is also what ``/`` serves."""

ASSET_DIRECTORIES: Final[tuple[str, ...]] = ("dist", "fonts", "js", "public", "vendor")
"""What those pages load. Everything else under ``web/`` is build input."""


def create_app(settings: Settings | None = None, *, service: Service | None = None) -> FastAPI:
    """Build the application over a real pipeline, a real store and a real engine.

    ``service`` is injectable so a test can supply one it already built — not so
    anything can be substituted for the engine. There is no fake mode in this
    project: the ``inline`` runner is the same search, run synchronously.
    """
    resolved = settings or Settings()
    state = service or build_service(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.warm()
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(
        title="TerraFolio",
        version=terrafolio.__version__,
        lifespan=lifespan,
        # One error shape (§1.7). FastAPI's own `{"detail": ...}` would be a
        # second, and a client cannot parse two.
        openapi_url="/openapi.json",
    )
    app.state.service = state
    install_error_handlers(app)
    app.include_router(pipeline_router)
    app.include_router(runs_router)

    _mount_web(app, resolved.web_dir)
    return app


def _mount_web(app: FastAPI, web_dir: Path) -> None:
    """Serve the four pages and the five asset directories, and nothing else."""
    if not web_dir.is_dir():
        return

    for name in ASSET_DIRECTORIES:
        directory = web_dir / name
        if directory.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=directory), name=f"web-{name}")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_dir / f"{PAGES[0]}.html")

    @app.get("/{page}.html", include_in_schema=False)
    def page(page: str) -> FileResponse:
        if page not in PAGES:
            raise ApiError(
                HTTPStatus.NOT_FOUND, ErrorCode.PAGE_NOT_FOUND, "No such page.", {"page": page}
            )
        return FileResponse(web_dir / f"{page}.html")
