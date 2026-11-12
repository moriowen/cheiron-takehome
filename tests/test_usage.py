import pytest

from trialviz.config import SUPPORTED_MODELS
from trialviz.llm.usage import PRICES_PER_1M, Usage, cost_usd


def test_every_supported_model_has_a_price():
    assert SUPPORTED_MODELS <= PRICES_PER_1M.keys()


def test_cost_for_nano():
    # gpt-5.4-nano: $0.20 in, $1.25 out per 1M tokens
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("gpt-5.4-nano", u) == pytest.approx(1.45)


def test_usage_adds_up():
    total = Usage(10, 2, 1) + Usage(5, 3, 0)
    assert total == Usage(input_tokens=15, output_tokens=5, reasoning_tokens=1)


def test_unknown_model_has_no_price():
    with pytest.raises(KeyError):
        cost_usd("gpt-4o-mini", Usage(1, 1))
