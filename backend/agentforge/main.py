"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentforge.api.routes import router
from agentforge.config import settings
from agentforge.storage.conversation_store import conversation_store


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


def main() -> None:
    """Run the development server."""
    import uvicorn

    uvicorn.run(
        "agentforge.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
