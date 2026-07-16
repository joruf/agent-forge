"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agentforge.api.routes import router
from agentforge.config import settings
from agentforge.llm.litellm_compat import ensure_litellm_proxy_package
from agentforge.storage.conversation_store import conversation_store

ensure_litellm_proxy_package()

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await conversation_store.initialize()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


def mount_production_frontend(application: FastAPI) -> None:
    """
    Serve the built Vite frontend when production mode is enabled.

    :param application: FastAPI application instance
    """
    if os.environ.get("AGENTFORGE_PROD") != "1":
        return
    if not FRONTEND_DIST.is_dir():
        return
    application.mount(
        "/",
        StaticFiles(directory=FRONTEND_DIST, html=True),
        name="frontend",
    )


mount_production_frontend(app)


def main() -> None:
    """Run the development or production server."""
    import uvicorn

    prod = os.environ.get("AGENTFORGE_PROD") == "1"
    uvicorn.run(
        "agentforge.main:app",
        host=settings.host,
        port=settings.port,
        reload=not prod,
    )


if __name__ == "__main__":
    main()
