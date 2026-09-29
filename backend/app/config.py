"""Application settings loaded from environment variables or the repo-root .env.

Secrets are held as ``SecretStr`` so they never appear in reprs, logs or
error messages. Use ``describe()`` for a log-safe summary.

Everything defaults to a configuration that works with no API keys:
SQLite on disk, memory backend "disabled", AI provider "none".
"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from typing import Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.enums import Channel, DealStage

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "backend" / "data" / "deal_rescue.db"

BANK_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Business records (source of truth).
    database_url: str = f"sqlite:///{DEFAULT_DB_PATH.as_posix()}"

    # AI provider boundary. Only "none" is implemented in M2; "anthropic" and
    # "ollama" are recognised but report "not implemented" (see app/ai/providers).
    ai_provider: Literal["none", "anthropic", "ollama"] = "none"
    ai_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = "qwen3:4b"  # smoke-tested M5 stage 0; must be pulled locally
    ollama_num_ctx: int = Field(default=4096, ge=1024, le=131072)
    ollama_think: bool = False  # qwen3 "thinking" adds latency; off by default

    # Deterministic deal intelligence (M4). JSON in env vars for the dict/list settings, e.g.
    # INTEL_STALL_DAYS_BY_STAGE='{"proposal": 10}'  INTEL_MEANINGFUL_CHANNELS='["call","meeting"]'
    intel_stall_days_default: int = Field(default=14, ge=1, le=365)
    intel_stall_days_by_stage: dict[str, int] = Field(default_factory=lambda: {
        "discovery": 21, "qualification": 14, "proposal": 10, "negotiation": 7, "closing": 5})
    intel_meaningful_channels: list[str] = Field(default_factory=lambda: ["call", "meeting", "email", "message"])
    intel_due_soon_days: int = Field(default=7, ge=0, le=90)
    intel_timezone: str = "UTC"  # business timezone that defines "today" for date-only deadlines

    # Memory boundary. "disabled" (default) never contacts Hindsight.
    memory_backend: Literal["disabled", "hindsight"] = "disabled"
    memory_max_attempts: int = Field(default=3, ge=1, le=5)  # quick retries inside one sync job
    # Durable sync worker (M3). Job-level retries with exponential backoff, persisted in SQLite.
    memory_worker_enabled: bool = True
    memory_worker_poll_seconds: float = Field(default=5.0, gt=0, le=3600)
    memory_sync_max_attempts: int = Field(default=5, ge=1, le=50)
    memory_retry_base_seconds: float = Field(default=30.0, gt=0, le=3600)
    memory_retry_max_seconds: float = Field(default=3600.0, gt=0, le=86400)
    memory_lease_seconds: float = Field(default=600.0, gt=0, le=86400)
    # Where Hindsight runs (M4.5). self_hosted (default): the Docker server at HINDSIGHT_BASE_URL.
    # cloud: Hindsight Cloud at HINDSIGHT_CLOUD_BASE_URL with HINDSIGHT_API_KEY as a Bearer token.
    hindsight_deployment: Literal["self_hosted", "cloud"] = "self_hosted"
    hindsight_base_url: str = "http://127.0.0.1:8888"  # self-hosted server
    hindsight_cloud_base_url: str = "https://api.hindsight.vectorize.io"  # verified in official docs
    # Hindsight Cloud API key (hsk_...). Sent ONLY in cloud mode, ONLY to hindsight_cloud_base_url.
    hindsight_api_key: SecretStr | None = None
    # Optional key for a self-hosted server with API-key auth enabled. Sent only in self_hosted mode.
    hindsight_self_hosted_api_key: SecretStr | None = None
    hindsight_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    # One bank per customer: <prefix><customer_id>. Shared bank for recorded outcomes.
    hindsight_customer_bank_prefix: str = Field(default="deal-rescue-cust-", pattern=r"^[A-Za-z0-9_-]{1,24}$")
    hindsight_outcomes_bank_id: str = Field(default="deal-rescue-outcomes", pattern=BANK_ID_PATTERN)
    # Leave unset in normal use (server default = LLM extraction). "chunks" stores text
    # without the LLM and exists only for credential-free integration tests.
    hindsight_extraction_mode: Literal["chunks", "concise", "verbose", "verbatim"] | None = None
    # Used only by the M1 spike script.
    hindsight_bank_id: str = Field(default="deal-rescue-demo", pattern=BANK_ID_PATTERN)

    @field_validator("hindsight_base_url", "hindsight_cloud_base_url", "ollama_base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("base URLs must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        if not value.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must be a sqlite:/// URL")
        return value

    @field_validator("anthropic_api_key", "hindsight_api_key", "hindsight_self_hosted_api_key", "ollama_model",
                     "hindsight_extraction_mode",
                     mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("intel_stall_days_by_stage")
    @classmethod
    def _validate_stage_days(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - set(get_args(DealStage))
        if unknown:
            raise ValueError(f"unknown deal stages in INTEL_STALL_DAYS_BY_STAGE: {sorted(unknown)}")
        if any(not isinstance(v, int) or v < 1 or v > 365 for v in value.values()):
            raise ValueError("INTEL_STALL_DAYS_BY_STAGE values must be integers between 1 and 365")
        return value

    @field_validator("intel_meaningful_channels")
    @classmethod
    def _validate_channels(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(get_args(Channel))
        if unknown:
            raise ValueError(f"unknown channels in INTEL_MEANINGFUL_CHANNELS: {sorted(unknown)}")
        if not value:
            raise ValueError("INTEL_MEANINGFUL_CHANNELS must not be empty")
        return sorted(set(value))

    @field_validator("intel_timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"INTEL_TIMEZONE is not a known IANA timezone: {value!r}") from None
        return value

    @model_validator(mode="after")
    def _lease_outlives_one_job(self):
        # A job may make memory_max_attempts calls of up to hindsight_timeout_seconds each;
        # a shorter lease would let a second worker reclaim a job that is still running.
        worst_case = self.hindsight_timeout_seconds * self.memory_max_attempts
        if self.memory_lease_seconds <= worst_case:
            raise ValueError(
                f"MEMORY_LEASE_SECONDS must exceed HINDSIGHT_TIMEOUT_SECONDS * MEMORY_MAX_ATTEMPTS ({worst_case:g})"
            )
        return self

    @model_validator(mode="after")
    def _cloud_is_safe(self):
        if self.hindsight_deployment == "cloud":
            parsed = urlparse(self.hindsight_cloud_base_url)
            loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")  # local fake servers in tests
            if parsed.scheme != "https" and not loopback:
                raise ValueError("HINDSIGHT_CLOUD_BASE_URL must use https (the API key is sent with every request)")
            if self.hindsight_extraction_mode is not None:
                raise ValueError("HINDSIGHT_EXTRACTION_MODE is a self-hosted test setting; Hindsight Cloud does not "
                                 "document extraction modes, so it must be empty in cloud mode")
        return self

    @property
    def hindsight_effective_base_url(self) -> str:
        return self.hindsight_cloud_base_url if self.hindsight_deployment == "cloud" else self.hindsight_base_url

    def hindsight_client_key(self) -> str | None:
        """The key to send to hindsight_effective_base_url (never the Cloud key to a local server)."""
        secret = self.hindsight_api_key if self.hindsight_deployment == "cloud" else self.hindsight_self_hosted_api_key
        return secret.get_secret_value() if secret else None

    def describe(self) -> dict[str, object]:
        """Log-safe view: reports whether secrets are set, never their values."""
        return {
            "database_url": self.database_url,
            "ai_provider": self.ai_provider,
            "anthropic_api_key": "set" if self.anthropic_api_key else "missing",
            "anthropic_model": self.anthropic_model,
            "memory_backend": self.memory_backend,
            "memory_worker_enabled": self.memory_worker_enabled,
            "hindsight_deployment": self.hindsight_deployment,
            "hindsight_base_url": self.hindsight_effective_base_url,
            "hindsight_api_key": "set" if self.hindsight_api_key else "not set",
            "hindsight_self_hosted_api_key": "set" if self.hindsight_self_hosted_api_key else "not set",
            "hindsight_customer_bank_prefix": self.hindsight_customer_bank_prefix,
            "hindsight_outcomes_bank_id": self.hindsight_outcomes_bank_id,
            "hindsight_bank_id": self.hindsight_bank_id,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
