from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.auth.cognito import CognitoConfig
from core.auth.options import BrowserOptions, validate_origin


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KINU_", case_sensitive=False, extra="ignore", env_ignore_empty=True
    )

    environment: str = "development"
    cognito_pool_id: str | None = None
    cognito_client_id: str | None = None
    cognito_domain: str | None = None
    database_url: SecretStr | None = None
    public_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def validate_cognito(self) -> "Settings":
        if self.cognito_pool_id is not None or self.cognito_client_id is not None:
            CognitoConfig(self.cognito_pool_id or "", self.cognito_client_id or "")
        self.public_url = validate_origin(self.public_url, self.environment == "development")
        if self.cognito_domain is not None:
            if self.cognito is None or self.database_url is None:
                raise ValueError("browser sign-in requires Cognito and application storage")
            if self.browser is None:
                raise ValueError("browser sign-in configuration is incomplete")
        return self

    @property
    def cognito(self) -> CognitoConfig | None:
        if self.cognito_pool_id is None or self.cognito_client_id is None:
            return None
        return CognitoConfig(self.cognito_pool_id, self.cognito_client_id)

    @property
    def browser(self) -> BrowserOptions | None:
        if self.cognito is None or self.cognito_domain is None:
            return None
        return BrowserOptions(
            self.cognito,
            self.cognito_domain,
            self.public_url,
        )
