"""Runtime settings, loaded from environment variables and `.env`."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Models that accept the same request shape (see OpenAIPlanner): max_completion_tokens,
# temperature=0, reasoning_effort="none", strict json_schema. Probed against the API on
# 2026-11-12. gpt-4o-mini, gpt-5 and gpt-5-mini reject part of that shape.
SUPPORTED_MODELS = frozenset({"gpt-5.4-nano", "gpt-5.4-mini", "gpt-5.4", "gpt-5.1"})


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: SecretStr
    llm_model: str = "gpt-5.4-mini"
    llm_max_completion_tokens: int = 1000
    llm_timeout_s: float = 30.0
    llm_max_attempts: int = 2  # first try + one repair retry

    @field_validator("llm_model")
    @classmethod
    def _check_model(cls, v: str) -> str:
        if v not in SUPPORTED_MODELS:
            raise ValueError(f"model {v!r} is not supported; use one of {sorted(SUPPORTED_MODELS)}")
        return v
