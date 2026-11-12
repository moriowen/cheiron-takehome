import json

import httpx2
import openai
import pytest

from tests.conftest import completion, fake_client
from tests.test_plan import make
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.planner import PlannerError
from trialviz.llm.prompt import PROMPT_VERSION
from trialviz.llm.usage import Usage
from trialviz.schemas import QueryRequest

REQ = QueryRequest(query="How are pembrolizumab trials distributed across phases?")
GOOD = json.dumps(make())
BAD_YEARS = json.dumps(make(filters=make()["filters"] | {"start_year": 2020, "end_year": 2010}))


def test_sends_only_swap_safe_params(settings):
    client = fake_client(completion(GOOD))
    OpenAIPlanner(settings, client=client).plan(REQ)
    call = client.chat.completions.calls[0]

    assert call["model"] == "gpt-5.4-nano"
    assert call["temperature"] == 0
    assert call["reasoning_effort"] == "none"
    assert call["max_completion_tokens"] == settings.llm_max_completion_tokens
    assert "max_tokens" not in call
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True


def test_user_query_and_hints_reach_the_model(settings):
    client = fake_client(completion(GOOD))
    req = QueryRequest(query="Trials per year?", drug_name="Pembrolizumab")
    OpenAIPlanner(settings, client=client).plan(req)
    user_msg = client.chat.completions.calls[0]["messages"][-1]["content"]
    assert "Trials per year?" in user_msg
    assert "Pembrolizumab" in user_msg


def test_returns_plan_with_usage_and_cost(settings):
    client = fake_client(completion(GOOD, prompt_tokens=1000, completion_tokens=100))
    result = OpenAIPlanner(settings, client=client).plan(REQ)

    assert result.plan.filters.drug == "pembrolizumab"
    assert result.attempts == 1
    assert result.model == "gpt-5.4-nano"
    assert result.prompt_version == PROMPT_VERSION
    assert result.usage == Usage(input_tokens=1000, output_tokens=100, reasoning_tokens=0)
    # 1000 * 0.20/1M + 100 * 1.25/1M
    assert result.cost_usd == pytest.approx(0.000325)


def test_repairs_once_after_validation_error(settings):
    client = fake_client(completion(BAD_YEARS), completion(GOOD))
    result = OpenAIPlanner(settings, client=client).plan(REQ)

    assert result.attempts == 2
    assert result.usage.input_tokens == 2000  # both calls are billed
    retry_messages = client.chat.completions.calls[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": BAD_YEARS}
    assert "start_year" in retry_messages[-1]["content"]


def test_gives_up_after_max_attempts(settings):
    client = fake_client(completion(BAD_YEARS), completion(BAD_YEARS))
    with pytest.raises(PlannerError, match="validation") as exc:
        OpenAIPlanner(settings, client=client).plan(REQ)
    assert exc.value.attempts == 2
    assert len(client.chat.completions.calls) == settings.llm_max_attempts


def test_invalid_json_is_repaired_too(settings):
    client = fake_client(completion("{not json"), completion(GOOD))
    assert OpenAIPlanner(settings, client=client).plan(REQ).attempts == 2


def test_refusal_fails_without_retry(settings):
    client = fake_client(completion(None, refusal="I can't help with that."))
    with pytest.raises(PlannerError, match="refused"):
        OpenAIPlanner(settings, client=client).plan(REQ)
    assert len(client.chat.completions.calls) == 1


def test_truncated_output_fails_without_retry(settings):
    client = fake_client(completion('{"supported": tr', finish_reason="length"))
    with pytest.raises(PlannerError, match="max_completion_tokens"):
        OpenAIPlanner(settings, client=client).plan(REQ)
    assert len(client.chat.completions.calls) == 1


def test_api_error_becomes_planner_error(settings):
    err = openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com"))
    client = fake_client(err)
    with pytest.raises(PlannerError, match="OpenAI API") as exc:
        OpenAIPlanner(settings, client=client).plan(REQ)
    assert isinstance(exc.value.__cause__, openai.APIConnectionError)
