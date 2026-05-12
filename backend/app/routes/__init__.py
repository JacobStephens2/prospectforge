"""HTTP API routers. Mount via app.routes.register(app)."""

from fastapi import FastAPI

from . import contacts, exports, runs, sources, suppressions, users


def register(app: FastAPI) -> None:
    """Wire all routers to the FastAPI app under /api."""
    app.include_router(users.router)
    app.include_router(sources.router)
    app.include_router(runs.router)
    app.include_router(contacts.router)
    app.include_router(exports.router)
    app.include_router(suppressions.router)
