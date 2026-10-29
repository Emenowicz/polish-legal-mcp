"""Application settings loaded from environment variables (prefix ``PLMCP_``)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLMCP_", env_file=".env", extra="ignore")

    env: Literal["development", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    # Authentication. Production requires GitHub or Google OAuth (see docs/security.md).
    auth_mode: Literal["none", "github", "google"] = "none"
    base_url: str | None = None
    jwt_signing_key: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    allowed_github_logins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Only Google accounts whose e-mail is verified by Google are matched.
    allowed_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra Host header values accepted besides the base_url host and loopback.
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    rate_limit_per_second: float = Field(default=2.0, gt=0)
    rate_limit_burst: int = Field(default=20, ge=1)

    # Upstream HTTP behaviour.
    http_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    http_max_retries: int = Field(default=3, ge=0, le=5)
    http_max_response_bytes: int = Field(default=10_000_000, ge=100_000)
    user_agent: str = "polish-legal-mcp/0.1 (+https://github.com/Emenowicz/polish-legal-mcp)"
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    cache_max_documents: int = Field(default=32, ge=1)

    @field_validator("allowed_github_logins", "allowed_emails", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_security(self) -> Settings:
        if self.auth_mode != "none":
            mode = self.auth_mode
            allowlist = "allowed_github_logins" if mode == "github" else "allowed_emails"
            required = ("base_url", f"{mode}_client_id", f"{mode}_client_secret", allowlist)
            missing = [name for name in required if not getattr(self, name)]
            if missing:
                raise ValueError(f"auth_mode={mode} requires: {', '.join(missing)}")
        if self.env == "production":
            if self.auth_mode == "none":
                raise ValueError(
                    "production requires auth_mode=github or google; refusing to start"
                )
            if not (self.base_url or "").startswith("https://"):
                raise ValueError("production requires an https:// base_url")
            key = self.jwt_signing_key.get_secret_value() if self.jwt_signing_key else ""
            if len(key) < 32:
                raise ValueError("production requires jwt_signing_key of at least 32 characters")
        return self

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS
