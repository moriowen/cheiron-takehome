"""Shared test doubles. No test outside tests/live/ touches the network."""

from types import SimpleNamespace

import pytest

from trialviz.config import LLMSettings


class FakeCompletions:
    """Stands in for `client.chat.completions`. Replays queued replies and records each call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def fake_client(*replies):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(replies)))


def completion(
    content: str | None,
    *,
    refusal: str | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 1000,
    completion_tokens: int = 100,
    reasoning_tokens: int = 0,
):
    """Builds an object shaped like an OpenAI ChatCompletion (only the fields we read)."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


@pytest.fixture
def settings() -> LLMSettings:
    return LLMSettings(_env_file=None, openai_api_key="test-key", llm_model="gpt-5.4-nano")
