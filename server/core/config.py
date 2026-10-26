from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.auth.cognito import CognitoConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KINU_", case_sensitive=False, extra="ignore")

    environment: str = "development"
    cognito_pool_id: str | None = None
    cognito_client_id: str | None = None

    @model_validator(mode="after")
    def validate_cognito(self) -> "Settings":
        if self.cognito_pool_id is not None or self.cognito_client_id is not None:
            CognitoConfig(self.cognito_pool_id or "", self.cognito_client_id or "")
        return self

    @property
    def cognito(self) -> CognitoConfig | None:
        if self.cognito_pool_id is None or self.cognito_client_id is None:
            return None
        return CognitoConfig(self.cognito_pool_id, self.cognito_client_id)
