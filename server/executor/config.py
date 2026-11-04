import re
from pathlib import Path

from core.config import Settings
from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict


class ExecutorSettings(Settings):
    model_config = SettingsConfigDict(hide_input_in_errors=True)
    vault_url: SecretStr = SecretStr("mongodb://localhost:27017/?replicaSet=kinu")
    vault_database: str = "kinu_private"
    vault_key: SecretStr | None = None
    vault_key_file: Path | None = None
    native_url: str = "http://agent:8001"
    native_token: SecretStr | None = None
    native_token_file: Path | None = None

    vercel_slug: str | None = None
    vercel_client_id: SecretStr | None = None
    vercel_client_secret: SecretStr | None = None
    vercel_client_secret_file: Path | None = None
    railway_client_id: SecretStr | None = None
    railway_client_secret: SecretStr | None = None
    railway_client_secret_file: Path | None = None

    @property
    def oauth_apps(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for provider in ("vercel", "railway"):
            values = {}
            for field in ("client_id", "client_secret"):
                secret = getattr(self, f"{provider}_{field}")
                values[field] = secret.get_secret_value().strip() if secret else ""
            if provider == "vercel":
                values["slug"] = (self.vercel_slug or "").strip()
            if not any(values.values()):
                continue
            if not all(values.values()):
                raise ValueError(f"Complete the KINU_{provider.upper()} OAuth settings")
            if any(
                len(value) > 8192 or any(ord(char) < 32 for char in value)
                for value in values.values()
            ):
                raise ValueError(f"Invalid KINU_{provider.upper()} OAuth settings")
            if provider == "vercel" and not re.fullmatch(r"[a-z0-9-]{1,80}", values["slug"]):
                raise ValueError("Invalid KINU_VERCEL_SLUG")
            result[provider] = values
        return result

    @model_validator(mode="after")
    def private_secrets(self) -> "ExecutorSettings":
        for key in ("vault_key", "native_token", "vercel_client_secret", "railway_client_secret"):
            path = getattr(self, key + "_file")
            if path:
                setattr(self, key, SecretStr(path.read_text().strip()))
        if not self.vault_key or len(self.vault_key.get_secret_value()) < 32:
            raise ValueError("executor requires its private encryption key")
        if not self.executor_token or not self.native_token:
            raise ValueError("executor requires private service authentication")
        _ = self.oauth_apps
        return self
