from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "tracerisk"
    postgres_user: str = "tracerisk"
    postgres_password: SecretStr = SecretStr("tracerisk_local_demo")
    jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    embedding_provider: str = "bge"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: int = 512
    embedding_distance: str = "cosine"
    chunk_schema_version: str = "v1"
    model_adapter: Literal["mock", "openai_compatible"] = "mock"
    model_base_url: str | None = None
    model_name: str | None = None
    model_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_model_configuration(self) -> "Settings":
        if self.model_adapter == "openai_compatible":
            if not self.model_base_url or not self.model_name or not self.model_api_key:
                raise ValueError("HTTP model requires explicit URL, model and key")
            url = urlsplit(self.model_base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or not self.model_name.strip()
                or not self.model_api_key.get_secret_value().strip()
            ):
                raise ValueError("Invalid HTTP model configuration")
        return self

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
