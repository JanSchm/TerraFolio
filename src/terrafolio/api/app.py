"""``create_app()`` — the factory, and the order the routes have to be mounted in.

A factory rather than a module-level ``app``: a test builds several applications
in one process, each against its own pipeline directory and its own database, and
a global would make them share state through the back door.

``web/`` is mounted **last and at the root**, so every API path is matched before
the static handler sees it. Mounting it first would shadow the whole API with a
404 from a directory that has no such file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import terrafolio
from terrafolio.api.errors import install_error_handlers
from terrafolio.api.routes_pipeline import router as pipeline_router
from terrafolio.api.routes_runs import router as runs_router
from terrafolio.api.service import Service, build_service
from terrafolio.api.settings import Settings

__all__ = ["create_app"]


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

    if resolved.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=resolved.web_dir, html=True), name="web")
    return app
