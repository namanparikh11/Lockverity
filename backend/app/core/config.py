"""Application configuration.

All runtime configuration is read from environment variables. No secrets
are committed to source control. Production configuration is validated
strictly; development configuration has safe defaults.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app._version import __version__

Environment = Literal["development", "test", "production"]


class Settings(BaseSettings):
    """Typed application configuration.

    Values are loaded from environment variables (prefix ``LOCKVERITY_``)
    and from a local ``.env`` file when one is present.
    """

    model_config = SettingsConfigDict(
        env_prefix="LOCKVERITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core runtime ---
    environment: Environment = Field(default="development")
    api_prefix: str = Field(default="/api/v1")
    cors_origins: list[str] = Field(default_factory=list)
    database_url: str = Field(default="sqlite:///./lockverity.sqlite")

    # --- Local control plane (LV-007) ---
    # The host and port the managing CLI bound this process to. The CLI
    # publishes both on the child's environment (``build_server_env``),
    # which lets the request policy pin the ``Host`` header to this
    # instance's exact authority - including the dynamic port the GUI
    # reserves. Both are ``None`` for a bare ``uvicorn app.main:app``,
    # where only the loopback hostname can be checked.
    cli_host: str | None = Field(default=None)
    cli_port: int | None = Field(default=None)
    # Developer-only override for the per-process control token. It lets
    # the two-port Vite workflow (where the dev server, not this
    # process, serves index.html) pin a token both sides know. It is
    # ignored in production, so the shipped runtime always mints a fresh
    # random token that never leaves memory.
    control_token: str | None = Field(default=None)

    # --- Workspace ---
    workspace_root: str = Field(default="./var/workspace")

    # --- Archive safety limits (bytes) ---
    archive_max_compressed_bytes: int = Field(default=100 * 1024 * 1024)
    archive_max_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024)
    archive_max_file_count: int = Field(default=100_000)
    archive_max_file_bytes: int = Field(default=256 * 1024 * 1024)
    archive_max_depth: int = Field(default=64)
    archive_suspicious_ratio: int = Field(default=200)

    # --- External provider safety (shared) ---
    provider_timeout_seconds: float = Field(default=15.0)
    provider_max_response_bytes: int = Field(default=10 * 1024 * 1024)
    provider_retry_limit: int = Field(default=2)

    # --- GitHub intake ---
    # The GitHub API origin is the canonical public
    # ``https://api.github.com`` host. A configurable
    # override was previously exposed as
    # ``LOCKVERITY_GITHUB_API_URL`` but it was unused in
    # the codebase (every URL builder hardcoded the
    # canonical host and the bounded HTTP client's
    # allowlist pinned the same two hosts). To remove a
    # dead configuration surface area and eliminate the
    # possibility of an operator pointing the analyzer at
    # an attacker-controlled host, the override is no
    # longer exposed. If a future release needs an
    # alternate API origin, the change must wire it
    # through every URL builder AND extend the bounded
    # HTTP allowlist with the same SSRF and
    # host-restriction validation applied to the
    # canonical origin.
    github_token: str | None = Field(default=None)
    github_timeout_seconds: float = Field(default=15.0)
    github_max_response_bytes: int = Field(default=10 * 1024 * 1024)
    github_max_download_bytes: int = Field(default=200 * 1024 * 1024)
    github_retry_limit: int = Field(default=2)
    github_user_agent: str = Field(default="lockverity/0.2 (core-intake)")

    # --- Scan worker / executor ---
    scan_worker_concurrency: int = Field(default=2, ge=1, le=32)
    scan_heartbeat_seconds: int = Field(default=15, ge=1, le=600)
    scan_heartbeat_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    scan_default_failure_summary_max_length: int = Field(default=500, ge=64, le=2048)
    scan_concurrency_per_repository: int = Field(default=1, ge=1, le=8)

    # --- Provider cache ---
    provider_cache_max_payload_bytes: int = Field(default=1024 * 1024, ge=4096)
    provider_cache_default_ttl_seconds: int = Field(default=3600, ge=60, le=7 * 24 * 3600)

    # --- Pagination ---
    pagination_default_page_size: int = Field(default=25, ge=1)
    pagination_max_page_size: int = Field(default=200, ge=1)

    # --- Application identity ---
    app_name: str = Field(default="Lockverity")
    # The version is read from the package's single source of truth
    # (``app._version``) so the API responses, the startup log, and
    # the export metadata never drift apart. See ``app/_version.py``
    # for the rationale.
    app_version: str = Field(default_factory=lambda: __version__)
    app_tagline: str = Field(
        default="Evidence-first software supply-chain assurance",
    )

    # --- Single-port production frontend serving ---
    # When ``serve_frontend`` is true, the FastAPI app hosts the
    # built React UI from the same host and port as the API. The
    # feature is opt-in: the default value is false so existing
    # development and test workflows are unchanged. Operators
    # enable the feature by setting ``LOCKVERITY_SERVE_FRONTEND=true``
    # (or, in a non-default ``.env``, the same key with a true
    # value) and by ensuring the Vite build output exists at the
    # configured ``frontend_dist`` path before starting the
    # application. The default path resolves to ``frontend/dist``
    # relative to the repository root (not the current working
    # directory) so the resolution is deterministic across
    # process invocation paths.
    serve_frontend: bool = Field(default=False)
    # The default dist path is relative to the repository root.
    # Operators may provide an absolute path to override the
    # default. The path is validated at startup: a missing
    # ``index.html`` or a non-existent directory is a fatal
    # startup error so a stale build is never served silently.
    frontend_dist: str = Field(default="frontend/dist")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow comma-separated CORS origins in env.

        ``LOCKVERITY_CORS_ORIGINS="https://a.example,https://b.example"``
        is easier to maintain in deployment than a JSON list.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _no_wildcard_in_production(cls, value: list[str], info) -> list[str]:
        env = info.data.get("environment", "development")
        if env == "production" and any(origin.strip() == "*" for origin in value):
            raise ValueError("Wildcard CORS origin '*' is not permitted in production.")
        return value

    @field_validator("pagination_max_page_size")
    @classmethod
    def _pagination_max_sane(cls, value: int) -> int:
        if value > 1000:
            raise ValueError("pagination_max_page_size must be <= 1000 to bound DB load.")
        return value

    @field_validator("archive_suspicious_ratio")
    @classmethod
    def _ratio_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("archive_suspicious_ratio must be a positive integer.")
        return value

    @field_validator("github_token")
    @classmethod
    def _token_must_not_look_like_url(cls, value: str | None) -> str | None:
        # Defence in depth: reject values that would obviously break
        # the transport layer (whitespace, control chars, surrounding
        # quotes that are sometimes pasted from shells).
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        stripped = value.strip()
        if any(ch in stripped for ch in ("\n", "\r", "\t", "\x00", " ")):
            raise ValueError("github_token must not contain whitespace or control characters.")
        if len(stripped) > 256:
            raise ValueError("github_token is unreasonably long.")
        return stripped

    @field_validator("frontend_dist")
    @classmethod
    def _frontend_dist_must_not_contain_traversal(cls, value: str) -> str:
        # The dist path is used as a static-file root. Path
        # traversal segments or backslashes would let an
        # attacker steer the resolver outside the intended
        # directory. The check is conservative: reject any
        # ``..`` segment (forward or backslash-separated) and
        # any absolute path that resolves outside the
        # repository layout. Absolute paths are still allowed
        # for operator overrides; the resolver performs the
        # containment check at startup.
        if not value or not value.strip():
            raise ValueError("frontend_dist must be a non-empty path.")
        normalised = value.strip()
        # Reject ``..`` traversal segments regardless of
        # separator so a Windows-style ``..\\`` cannot bypass
        # a forward-slash check.
        if ".." in normalised.replace("\\", "/").split("/"):
            raise ValueError("frontend_dist must not contain '..' traversal segments.")
        return normalised

    @field_validator("serve_frontend")
    @classmethod
    def _serve_frontend_production_only(cls, value: bool, info) -> bool:
        # The single-port runtime is intended for production
        # deployment. In development and test environments the
        # Vite dev server is the supported workflow; the
        # backend should never try to serve a Vite build
        # output in those modes. This is defence in depth: an
        # operator who sets the flag in development gets a
        # clear startup error rather than a confusing
        # partially-served UI.
        env = info.data.get("environment", "development")
        if value and env != "production":
            raise ValueError(
                "LOCKVERITY_SERVE_FRONTEND=true is only supported when "
                "LOCKVERITY_ENVIRONMENT=production. Use the Vite dev "
                "server in development and test."
            )
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def repo_root(self) -> Path:
        """Absolute path to the repository root.

        The repository root is the parent of the ``backend``
        package directory. The application always resolves
        relative paths against this root so a frontend dist
        at ``frontend/dist`` works the same way regardless of
        the operator's current working directory.
        """
        # ``config.py`` lives at ``backend/app/core/config.py``;
        # parents[0] is ``core/``, parents[1] is ``app/``,
        # parents[2] is ``backend/``, parents[3] is the repo
        # root.
        return Path(__file__).resolve().parents[3]

    @property
    def frontend_dist_path(self) -> Path:
        """Absolute path to the frontend dist directory.

        In source mode (no ``sys._MEIPASS``) the
        configured ``frontend_dist`` is resolved
        deterministically against the repository
        root. The ``LOCKVERITY_FRONTEND_DIST`` env var
        is honoured so an operator can point at an
        alternate dist without editing the source.

        In frozen mode the bundled dist under
        ``sys._MEIPASS/frontend/dist`` always wins
        regardless of the configured ``frontend_dist``.
        The portable package ships a single, versioned
        dist and the operator cannot accidentally
        redirect to a stale source checkout. The
        frozen-mode behaviour is the documented
        v2.1 Part B3A single-port portable contract.

        The result is not validated for existence
        here; the :mod:`app.static_frontend` module
        performs the startup validation when serving
        is enabled.
        """
        # Frozen mode wins unconditionally: the
        # portable package ships a single bundled dist
        # under ``sys._MEIPASS/frontend/dist`` and the
        # operator cannot redirect it. The check is
        # the single source of truth for "is the
        # runtime a frozen artefact?"; every other
        # path in the application funnels through
        # :func:`app.runtime_paths.is_frozen` so a
        # future build flavour lands in one place.
        if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
            return Path(sys._MEIPASS) / "frontend" / "dist"
        # Source mode: honour an absolute override;
        # otherwise resolve relative to the repo root.
        candidate = Path(self.frontend_dist).expanduser()
        if not candidate.is_absolute():
            candidate = (self.repo_root / candidate).resolve()
        return candidate


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance.

    Caching means tests must call :func:`get_settings.cache_clear` after
    mutating the environment if they need to re-evaluate configuration.
    """
    return Settings()
