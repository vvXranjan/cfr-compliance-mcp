"""Typed runtime configuration for cfr-compliance-mcp.

All environment-driven settings are declared here as a single Pydantic
`Settings` model. Nothing else in the codebase should call `os.environ`
directly — every other module receives configuration by importing
`get_settings()` from here. This keeps configuration testable (you can
construct a `Settings(...)` with overrides in tests) and gives us
validation for free (e.g. a malformed timeout in `.env` fails fast at
startup instead of causing a confusing error mid-request).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- eCFR API ---
    ecfr_base_url: str = Field(default="https://www.ecfr.gov")
    ecfr_request_timeout_seconds: float = Field(default=30.0, gt=0)

    # --- Retry / backoff ---
    ecfr_max_retries: int = Field(default=3, ge=0, le=10)
    ecfr_retry_backoff_base_seconds: float = Field(default=1.0, gt=0)

    # --- Client-side rate limiting ---
    ecfr_max_requests_per_minute: int = Field(default=60, gt=0)

    # --- Caching ---
    cache_backend: Literal["memory", "redis"] = Field(default="memory")
    cache_ttl_seconds: int = Field(default=3600, gt=0)
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- Compliance Memory (deterministic, durable, advisory) ---
    # Historical, VERIFIED outcomes are persisted as an append-only JSONL
    # store and may be used as contextual precedent only -- never as a
    # source of regulatory truth. Opt-in: defaults to the authoritative-only
    # pipeline. Set cfr_memory_enabled=false explicitly to force it (memory
    # failures are always fail-open regardless).
    cfr_memory_enabled: bool = Field(default=False)
    cfr_memory_dir: str = Field(default="")

    # --- Persistence ---
    # Analysis/report persistence backend. "file" (default) persists
    # auditable JSON reports via agent.reporting. "postgres" is recognized
    # but NOT implemented yet -- selecting it fails clearly at repository
    # construction (never a silent fallback to "file"). PostgreSQL will be
    # introduced only when the dashboard/review/history layer needs
    # queryable persistence.
    cfr_persistence_backend: Literal["file", "postgres"] = Field(default="file")
    cfr_database_url: str = Field(default="")

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "text"] = Field(default="text")

    # --- MCP server transport ---
    mcp_transport: Literal["stdio", "streamable-http"] = Field(default="stdio")
    mcp_http_host: str = Field(default="127.0.0.1")
    mcp_http_port: int = Field(default=8000, gt=0, lt=65536)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide `Settings` singleton.

    Cached with `lru_cache` so `.env` is parsed once per process, not on
    every call. Tests that need different settings should construct
    `Settings(...)` directly rather than relying on this singleton.
    """
    return Settings()
