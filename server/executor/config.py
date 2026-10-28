from pathlib import Path

from core.config import Settings
from pydantic import SecretStr, model_validator


class ExecutorSettings(Settings):
    vault_url: SecretStr = SecretStr("mongodb://localhost:27017/?replicaSet=kinu")
    vault_database: str = "kinu_private"
    vault_key: SecretStr | None = None
    vault_key_file: Path | None = None
    native_url: str = "http://agent:8001"
    native_token: SecretStr | None = None
    native_token_file: Path | None = None

    @model_validator(mode="after")
    def private_secrets(self) -> "ExecutorSettings":
        for key in ("vault_key", "native_token"):
            path = getattr(self, key + "_file")
            if path:
                setattr(self, key, SecretStr(path.read_text().strip()))
        if not self.vault_key or len(self.vault_key.get_secret_value()) < 32:
            raise ValueError("executor requires its private encryption key")
        if not self.executor_token or not self.native_token:
            raise ValueError("executor requires private service authentication")
        return self
