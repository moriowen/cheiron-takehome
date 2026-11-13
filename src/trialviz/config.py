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


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    max_records: int = 20_000  # fetch every record up to this many matches per series (D33)
    sample_records: int = 3000  # above max_records: relevance-order sample size (D08, D33)
    cache_ttl_s: float = 3600.0
    cache_max_entries: int = 512  # least recently used entries are evicted past this
    request_budget_s: float = 90.0  # registry time per request before HTTP 504
    # Measured API limit: about 10 requests in a burst, then about 1/s (docs/01). 1.0 still
    # got some 429s in the e2e eval, so the refill is likely a little under 1/s.
    ctgov_rate_per_s: float = 0.8
    ctgov_burst: int = 8
