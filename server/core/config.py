from pathlib import Path

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.auth.options import BrowserOptions, validate_origin


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KINU_", case_sensitive=False, extra="ignore", env_ignore_empty=True
    )

    environment: str = "development"
    mongo_url: SecretStr = SecretStr("mongodb://localhost:27017/?replicaSet=kinu")
    mongo_database: str = "kinu"
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    auth_secret: SecretStr | None = None
    session_hours: int = 12
    resend_key: SecretStr | None = None
    setup_token: SecretStr | None = None
    setup_token_file: Path | None = None
    email_sender: str = "Kinu <noreply@example.com>"
    aws_region: str = "eu-west-2"
    executor_url: str = "http://executor:8002"
    executor_token: SecretStr | None = None
    executor_token_file: Path | None = None
    auth_secret_file: Path | None = None
    connection_principals: str | None = None
    public_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        for key in ("auth_secret", "executor_token", "setup_token"):
            path = getattr(self, key + "_file")
            if path:
                setattr(self, key, SecretStr(path.read_text().strip()))
        self.public_url = validate_origin(self.public_url, self.environment == "development")
        if self.auth_secret and len(self.auth_secret.get_secret_value()) < 32:
            raise ValueError("authentication secret requires at least 32 characters")
        if not 1 <= self.session_hours <= 24:
            raise ValueError("session lifetime must be between 1 and 24 hours")
        return self

    @property
    def has_storage(self) -> bool:
        return bool(self.mongo_url)

    @property
    def browser(self) -> BrowserOptions | None:
        return BrowserOptions(self.public_url, self.session_hours) if self.auth_secret else None
