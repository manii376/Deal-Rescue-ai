import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.provider import AIProvider
from app.ai.service import build_ai_service
from app.api.errors import install_error_handlers
from app.api.routers import (
    ai,
    commitments,
    customers,
    deals,
    intelligence,
    interactions,
    memory,
    stakeholders,
    system,
    timemachine,
)
from app.api.routers.system import APP_VERSION
from app.config import get_settings
from app.db.engine import create_db_engine, sqlite_path
from app.db.migrations import migrate
from app.intelligence.rules import IntelConfig
from app.intelligence.service import IntelligenceService
from app.memory.service import MemoryService, build_memory_service
from app.memory.sync import MemorySyncWorker, SyncPolicy

logger = logging.getLogger("deal_rescue")


def create_app(memory_service: MemoryService | None = None, ai_provider: AIProvider | None = None) -> FastAPI:
    """Build the app. Services come from settings at startup unless injected (tests)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        # describe() reports whether secrets are set, never their values.
        logger.info("Starting Deal Rescue AI with config: %s", settings.describe())
        engine = create_db_engine(settings.database_url)
        migrate(engine, sqlite_path(settings.database_url))
        app.state.engine = engine
        app.state.memory = memory_service or build_memory_service(settings)
        app.state.ai = build_ai_service(settings, provider=ai_provider)
        app.state.sync_policy = SyncPolicy.from_settings(settings)
        app.state.intelligence = IntelligenceService(IntelConfig.from_settings(settings))
        worker = None
        if app.state.memory.backend != "disabled":
            worker = MemorySyncWorker(engine, app.state.memory, app.state.sync_policy,
                                      poll_seconds=settings.memory_worker_poll_seconds)
            if settings.memory_worker_enabled:
                await worker.start()  # first pass recovers work left over from a previous run
        app.state.memory_worker = worker
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await app.state.memory.aclose()
            engine.dispose()

    app = FastAPI(title="Deal Rescue AI", version=APP_VERSION, lifespan=lifespan)
    install_error_handlers(app)
    for module in (system, customers, deals, stakeholders, interactions, commitments, memory, intelligence,
                   ai, timemachine):
        app.include_router(module.router)
    app.include_router(memory.status_router)
    return app


app = create_app()
