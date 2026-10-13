"""Runtime configuration.

Everything comes from environment variables so that the model, endpoint and keys can be
changed with `docker compose up -d` and no image rebuild. Secrets are `SecretStr` so they
never show up in reprs, logs or tracebacks.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # An empty variable (`LLM_EXTRA_BODY: ${LLM_EXTRA_BODY:-}` in compose) means "use the default".
    model_config = SettingsConfigDict(env_file=None, extra="ignore", env_ignore_empty=True)

    log_level: str = "INFO"

    database_url: SecretStr
    redis_url: SecretStr

    # Shared volume for uploads and generated files (mounted into api and workers).
    data_dir: Path = Path("/data")

    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = ""
    llm_api_key: SecretStr = SecretStr("")
    # Extra request fields for this endpoint, as JSON. Qwen's thinking mode is turned off here:
    # vLLM/SGLang take {"chat_template_kwargs": {"enable_thinking": false}},
    # OpenRouter takes {"reasoning": {"enabled": false}}.
    llm_extra_body: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}}
    )
    # Ask the server for a JSON object (response_format). Off by default: not every
    # OpenAI-compatible server supports it, and the agent validates the JSON itself anyway.
    llm_json_mode: bool = False
    llm_timeout_s: float = Field(default=240, gt=0)
    llm_max_tokens: int = Field(default=16_000, ge=1_000)

    # Session cookie. Secure=true behind HTTPS; the local `docker compose up` serves plain HTTP.
    cookie_secure: bool = False
    session_days: int = Field(default=14, ge=1, le=90)

    # Hard limits from the brief: up to 5 concurrent generations, 10 files, 20 MB each.
    max_files_per_project: int = Field(default=10, ge=1)
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    # Our own caps so one account cannot fill the disk.
    max_projects_per_user: int = Field(default=100, ge=1)
    max_bytes_per_user: int = Field(default=1024 * 1024 * 1024, ge=1)

    # Swagger UI and the OpenAPI schema: handy locally, a map of the API for anyone otherwise.
    enable_api_docs: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
