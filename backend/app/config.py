"""Runtime configuration.

Everything comes from environment variables so that the model, endpoint and keys can be
changed with `docker compose up -d` and no image rebuild. Secrets are `SecretStr` so they
never show up in reprs, logs or tracebacks.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    log_level: str = "INFO"

    database_url: SecretStr
    redis_url: SecretStr

    # Shared volume for uploads and generated files (mounted into api and workers).
    data_dir: Path = Path("/data")

    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")

    # Hard limits from the brief: up to 5 concurrent generations, 10 files, 20 MB each.
    max_files_per_project: int = Field(default=10, ge=1)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
