from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KINU_", case_sensitive=False, extra="ignore")

    environment: str = "development"
