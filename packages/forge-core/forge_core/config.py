"""Runtime configuration.

All settings come from the environment (12-factor). There are **no default
credentials**: secret-bearing fields have no usable default and the application
refuses to start in non-development environments without them.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"
    service_name: str = "forge-api"
    version: str = "0.1.0"

    # --- datastores -------------------------------------------------------
    #: SQLAlchemy async DSN. When empty, the embedded PostgreSQL server is used
    #: (development only) and this is populated at startup.
    database_url: str = ""
    database_pool_size: int = 10
    database_max_overflow: int = 10
    #: Statement timeout applied to every pooled connection, in milliseconds.
    database_statement_timeout_ms: int = 30_000

    redis_url: str = ""

    # --- security ---------------------------------------------------------
    #: HMAC key for session tokens. Auto-generated in development/test only.
    secret_key: SecretStr = SecretStr("")
    access_token_ttl_seconds: int = 3600
    #: GitHub webhook HMAC secret. Webhooks are rejected outright when unset.
    github_webhook_secret: SecretStr = SecretStr("")
    cors_allow_origins: tuple[str, ...] = ("http://localhost:5173",)
    #: Cookie-based auth requires CSRF defence; disabled only for pure-bearer clients.
    csrf_protection_enabled: bool = True

    #: Development-only bootstrap administrator. Both must be set for it to apply,
    #: and it is refused outside development.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: SecretStr = SecretStr("")

    rate_limit_enabled: bool = True
    rate_limit_login_per_minute: int = 10
    rate_limit_write_per_minute: int = 60
    rate_limit_webhook_per_minute: int = 120

    # --- orchestration ----------------------------------------------------
    worker_concurrency: int = 4
    worker_poll_interval_seconds: float = 0.5
    #: A claimed job whose lease is not renewed within this window is reclaimed.
    job_lease_seconds: int = 60
    job_heartbeat_seconds: float = 15.0
    job_max_attempts: int = 3
    job_backoff_base_seconds: float = 2.0
    job_backoff_max_seconds: float = 300.0
    #: Concurrency guards.
    max_concurrent_pipelines_per_repository: int = 1

    # --- build ------------------------------------------------------------
    workspace_root: Path = Path(".forge/workspaces")
    artifact_root: Path = Path(".forge/artifacts")
    log_root: Path = Path(".forge/logs")
    build_timeout_seconds: int = 900
    build_max_log_bytes: int = 4 * 1024 * 1024
    build_memory_limit_mb: int = 2048
    build_cpu_seconds: int = 600
    build_max_processes: int = 256
    build_max_file_bytes: int = 512 * 1024 * 1024
    #: The process builder is not a kernel sandbox. It refuses to run unless this
    #: is explicitly set, so the weaker isolation mode is never entered by accident.
    allow_unsandboxed_builds: bool = False
    #: Directories prepended to the build PATH so manifest-declared tools (pytest,
    #: ruff, npm...) can be found. The process builder shares the host toolchain —
    #: this makes exactly which directories are exposed explicit and auditable
    #: rather than implicit. Empty means "the interpreter's own bin directory".
    build_toolchain_path: str = ""
    builder: Literal["process", "buildkit"] = "process"
    registry: Literal["local", "ecr"] = "local"
    deploy_adapter: Literal["process", "docker"] = "process"

    # --- delivery ---------------------------------------------------------
    router_admin_url: str = "http://127.0.0.1:8099"
    router_public_host: str = "127.0.0.1"
    canary_steps: tuple[int, ...] = (10, 25, 50, 100)
    canary_step_seconds: int = 20
    #: Health gates. A candidate is promoted only on an explicit HEALTHY verdict.
    health_min_samples: int = 20
    health_max_error_rate: float = 0.05
    health_max_p95_latency_ms: float = 750.0
    health_sample_staleness_seconds: int = 60
    deployment_lock_ttl_seconds: int = 900

    # --- observability ----------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_endpoint: str = ""

    @field_validator("cors_allow_origins", "canary_steps", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated environment values for tuple fields."""
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
            return tuple(items)
        return value

    @field_validator("canary_steps")
    @classmethod
    def _check_steps(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        steps = tuple(int(v) for v in value)
        if not steps:
            raise ValueError("canary_steps must not be empty")
        if any(not 0 < s <= 100 for s in steps):
            raise ValueError("canary_steps entries must be within 1..100")
        if list(steps) != sorted(steps):
            raise ValueError("canary_steps must be ascending")
        if steps[-1] != 100:
            raise ValueError("canary_steps must finish at 100")
        return steps

    @field_validator("health_max_error_rate")
    @classmethod
    def _check_rate(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("health_max_error_rate must be a fraction within 0..1")
        return value

    @field_validator("workspace_root", "artifact_root", "log_root")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """Resolve runtime paths against the working directory once, at load time.

        Workers, builders and the router start subprocesses with their own working
        directories; a relative path would otherwise resolve differently per process.
        """
        return value if value.is_absolute() else (Path.cwd() / value).resolve()

    @property
    def is_development(self) -> bool:
        return self.environment in ("development", "test")

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        if self.is_development:
            # Ephemeral key: tokens do not survive a restart, which is correct for dev.
            if not self.secret_key.get_secret_value():
                object.__setattr__(self, "secret_key", SecretStr(secrets.token_urlsafe(48)))
            return self

        missing = [
            name
            for name, val in (
                ("FORGE_SECRET_KEY", self.secret_key.get_secret_value()),
                ("FORGE_DATABASE_URL", self.database_url),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"Missing required configuration for environment "
                f"{self.environment!r}: {', '.join(missing)}"
            )
        if len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("FORGE_SECRET_KEY must be at least 32 characters")
        if self.bootstrap_admin_password.get_secret_value():
            raise ValueError(
                "Bootstrap administrator credentials are a development-only feature "
                "and must not be set outside development."
            )
        if "*" in self.cors_allow_origins:
            raise ValueError("Wildcard CORS origin is not permitted outside development.")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache. Used by tests that manipulate the environment."""
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache"]
