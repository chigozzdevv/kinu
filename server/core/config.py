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
    database_url: SecretStr | None = None
    aws_region: str = "eu-west-2"
    data_api_cluster_arn: str | None = None
    data_api_secret_arn: str | None = None
    data_api_database: str = "kinu"
    demo_organisation: str | None = None
    demo_queue_url: str | None = None
    public_url: str = "http://localhost:5173"

    @model_validator(mode="after")
    def validate_cognito(self) -> "Settings":
        if bool(self.data_api_cluster_arn) != bool(self.data_api_secret_arn):
            raise ValueError("Data API storage requires both cluster and secret ARNs")
        if self.database_url and self.data_api_cluster_arn:
            raise ValueError("configure one application database transport")
        if self.cognito_pool_id is not None or self.cognito_client_id is not None:
            CognitoConfig(self.cognito_pool_id or "", self.cognito_client_id or "")
        self.public_url = validate_origin(self.public_url, self.environment == "development")
        return self

    @property
    def has_storage(self) -> bool:
        return bool(self.database_url or self.data_api_cluster_arn)

    @property
    def cognito(self) -> CognitoConfig | None:
        if self.cognito_pool_id is None or self.cognito_client_id is None:
            return None
        return CognitoConfig(self.cognito_pool_id, self.cognito_client_id)

    @property
    def browser(self) -> BrowserOptions | None:
        if self.cognito is None or not self.has_storage:
            return None
        return BrowserOptions(
            self.cognito,
            self.public_url,
        )
