from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="URJA_", env_file=".env", extra="ignore"
    )

    base_url: str = "https://urja-ops.flockenergy.tech"
    email: str = ""
    password: SecretStr = SecretStr("")
    timeout: float = Field(default=20, gt=0, le=120)
    api_key: SecretStr = SecretStr("")
    cache_seconds: int = Field(default=60, ge=0, le=3600)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.netloc
            or parts.username
            or parts.password
        ):
            raise ValueError("URJA_BASE_URL must be an HTTPS origin")
        if parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError(
                "URJA_BASE_URL must not contain a path, query, or fragment"
            )
        return value.rstrip("/")
