from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.ai.schemas import AIStatus
from app.api.deps import AIDep, MemoryDep
from app.config import get_settings
from app.db.migrations import LATEST_VERSION
from app.memory.types import MemoryStatus
from app.services.hindsight_memory import from_settings

APP_VERSION = "0.2.0"

router = APIRouter(tags=["system"])


@router.get("/api/health")
async def health() -> dict[str, str]:
    """Liveness of this backend only; does not depend on Hindsight or any AI provider."""
    return {"status": "ok", "service": "deal-rescue-backend", "version": APP_VERSION}


@router.get("/api/health/dependencies")
async def dependency_health() -> dict[str, object]:
    """Diagnostic: is a Hindsight server reachable at HINDSIGHT_BASE_URL (regardless of MEMORY_BACKEND)?"""
    settings = get_settings()
    probe = from_settings(settings)
    try:
        status = await probe.health()
    finally:
        await probe.aclose()
    return {
        "hindsight": {
            "reachable": status.reachable,
            "healthy": status.healthy,
            "api_version": status.api_version,
            "base_url": settings.hindsight_effective_base_url,
            "deployment": settings.hindsight_deployment,
            "api_key": "set" if settings.hindsight_client_key() else "not set",
        },
        "anthropic": {"api_key": "set" if settings.anthropic_api_key else "missing"},
    }


class DatabaseStatus(BaseModel):
    available: bool
    schema_version: int | None
    expected_schema_version: int


class Capabilities(BaseModel):
    database: DatabaseStatus
    memory: MemoryStatus
    ai: AIStatus


@router.get("/api/system/capabilities", response_model=Capabilities,
            summary="What works right now: database, memory backend, AI provider")
async def capabilities(request: Request, memory: MemoryDep, ai: AIDep) -> Capabilities:
    try:
        with request.app.state.engine.connect() as conn:
            version = conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar()
        db = DatabaseStatus(available=True, schema_version=version, expected_schema_version=LATEST_VERSION)
    except Exception:
        db = DatabaseStatus(available=False, schema_version=None, expected_schema_version=LATEST_VERSION)
    return Capabilities(database=db, memory=await memory.status(), ai=await ai.status())
