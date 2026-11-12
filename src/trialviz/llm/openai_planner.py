"""OpenAI implementation of Planner. The only module that imports the OpenAI client."""

import logging
import time

import openai
from pydantic import ValidationError

from trialviz.config import LLMSettings
from trialviz.llm.planner import PlannerError, PlanResult
from trialviz.llm.prompt import PROMPT_VERSION, build_messages, repair_message
from trialviz.llm.usage import Usage, cost_usd
from trialviz.plan import QueryPlan, plan_json_schema
from trialviz.schemas import QueryRequest

log = logging.getLogger(__name__)

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "QueryPlan", "strict": True, "schema": plan_json_schema()},
}


class OpenAIPlanner:
    def __init__(self, settings: LLMSettings, client: openai.OpenAI | None = None):
        self._settings = settings
        self._client = client or openai.OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.llm_timeout_s,
            max_retries=2,  # SDK retries connection errors, 429 and 5xx with backoff
        )

    def plan(self, request: QueryRequest) -> PlanResult:
        s = self._settings
        messages = build_messages(request)
        usage = Usage()

        for attempt in range(1, s.llm_max_attempts + 1):
            completion = self._call(messages, attempt)
            usage += _usage_of(completion)
            choice = completion.choices[0]

            if choice.message.refusal:
                raise PlannerError(f"model refused: {choice.message.refusal}", attempts=attempt)
            if choice.finish_reason == "length":
                raise PlannerError(
                    "output truncated; raise llm_max_completion_tokens", attempts=attempt
                )

            content = choice.message.content or ""
            try:
                plan = QueryPlan.model_validate_json(content)
            except ValidationError as e:
                errors = _format_errors(e)
                log.warning("plan failed validation (attempt %d): %s", attempt, errors)
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    repair_message(errors),
                ]
                continue

            return PlanResult(
                plan=plan,
                model=s.llm_model,
                prompt_version=PROMPT_VERSION,
                attempts=attempt,
                usage=usage,
                cost_usd=cost_usd(s.llm_model, usage),
            )

        raise PlannerError(
            f"plan failed validation after {s.llm_max_attempts} attempts",
            attempts=s.llm_max_attempts,
        )

    def _call(self, messages: list[dict[str, str]], attempt: int):
        s = self._settings
        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=s.llm_model,
                messages=messages,
                response_format=_RESPONSE_FORMAT,
                temperature=0,
                reasoning_effort="none",
                max_completion_tokens=s.llm_max_completion_tokens,
            )
        except openai.OpenAIError as e:
            raise PlannerError(f"OpenAI API call failed: {e}", attempts=attempt) from e

        u = _usage_of(completion)
        log.info(
            "planner call model=%s attempt=%d in=%d out=%d reasoning=%d cost=$%.6f latency=%.2fs",
            s.llm_model, attempt, u.input_tokens, u.output_tokens, u.reasoning_tokens,
            cost_usd(s.llm_model, u), time.perf_counter() - started,
        )  # fmt: skip
        return completion


def _usage_of(completion) -> Usage:
    u = completion.usage
    details = getattr(u, "completion_tokens_details", None)
    return Usage(
        input_tokens=u.prompt_tokens,
        output_tokens=u.completion_tokens,
        reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0,
    )


def _format_errors(e: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(map(str, err['loc'])) or '(root)'}: {err['msg']}" for err in e.errors()
    )
