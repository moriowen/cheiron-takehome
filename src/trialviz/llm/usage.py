"""Token usage and cost. The API key cannot read billing, so we track spend ourselves."""

from dataclasses import dataclass

# USD per 1M tokens (input, output), standard tier.
# Source: developers.openai.com/api/docs/pricing, read 2026-11-12.
PRICES_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.1": (1.25, 10.00),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes reasoning tokens, which are billed as output
    reasoning_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
        )


def cost_usd(model: str, usage: Usage) -> float:
    price_in, price_out = PRICES_PER_1M[model]
    return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000
