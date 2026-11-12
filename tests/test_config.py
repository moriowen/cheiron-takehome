import pytest
from pydantic import ValidationError

from trialviz.config import SUPPORTED_MODELS, LLMSettings


def test_defaults_to_dev_model():
    s = LLMSettings(_env_file=None, openai_api_key="k")
    assert s.llm_model == "gpt-5.4-nano"


@pytest.mark.parametrize("model", sorted(SUPPORTED_MODELS))
def test_accepts_supported_models(model):
    assert LLMSettings(_env_file=None, openai_api_key="k", llm_model=model).llm_model == model


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-5-mini", "gpt-5", "made-up"])
def test_rejects_models_with_incompatible_params(model):
    with pytest.raises(ValidationError, match="not supported"):
        LLMSettings(_env_file=None, openai_api_key="k", llm_model=model)


def test_api_key_is_hidden_in_repr():
    s = LLMSettings(_env_file=None, openai_api_key="sk-proj-secret")
    assert "sk-proj-secret" not in repr(s)
    assert s.openai_api_key.get_secret_value() == "sk-proj-secret"


def test_missing_key_fails(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        LLMSettings(_env_file=None)
