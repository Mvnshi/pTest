"""ASGI entrypoint. Serves the API and, when it has been built, the SPA."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import storage
from .api import router
from .config import ENGINE_VERSION, FRONTEND_DIST

@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init()
    yield


app = FastAPI(
    title="ProofTrade",
    lifespan=lifespan,
    version=ENGINE_VERSION,
    description=(
        "Turns a plain-English trading strategy into a typed DSL, backtests it on daily "
        "bars, and reports how much the result can be trusted. No real-money trading, no "
        "broker integration, no generated code."
    ),
)

# The Vite dev server proxies /api, so CORS only matters if the SPA is served from a
# different origin during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")
