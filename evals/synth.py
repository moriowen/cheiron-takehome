"""Synthetic eval cases generated along explicit dimensions.

Each case starts as a tuple (intent x entity type x filter x phrasing style). Code derives the
expected plan from the tuple, so labels are correct by construction. An LLM only turns the
tuple into a natural question, and a deterministic check rejects any question that drops an
entity or a constraint. Review the output before committing it.

Run: uv run python -m evals.synth [--n 40] [--seed 7]
Writes evals/data/synthetic.json. Bump evals.dataset.DATASET_VERSION after regenerating.
"""

import argparse
import itertools
import json
import random
from typing import NamedTuple

import openai

from evals.dataset import SYNTHETIC_FILE
from trialviz.config import LLMSettings

GENERATOR_MODEL = "gpt-5.4"  # different from the planner, to avoid grading its own phrasing


class Tuple(NamedTuple):
    intent: str
    entity: str  # drug | condition | sponsor | none
    filter: str  # none | status | phase | years | country
    style: str  # terse | conversational | formal | lowercase_typo | indirect


ENTITIES = {
    "drug": ["semaglutide", "nivolumab", "metformin", "adalimumab", "trastuzumab", "remdesivir"],
    "condition": ["asthma", "psoriasis", "glioblastoma", "heart failure", "lupus", "migraine"],
    "sponsor": ["Pfizer", "Novartis", "AstraZeneca", "Mayo Clinic"],
}
FILTERS = {
    "none": ({}, []),
    "status": ({"status": ["RECRUITING"]}, ["recruit"]),
    "phase": ({"phase": ["PHASE3"]}, ["3"]),
    "years": ({"start_year": 2018}, ["2018"]),
    "country": ({"country": "Germany"}, ["germany"]),
}
STYLES = {
    "terse": "a terse keyword-style query, under 12 words",
    "conversational": "a casual question a clinician might type",
    "formal": "a formal request from a market analyst",
    "lowercase_typo": "all lowercase with one small typo in a common word (never in a name)",
    "indirect": "an indirect phrasing that implies the analysis without naming the chart",
}
# intent -> (allowed entity types, allowed filters, expected plan core, description for the LLM)
INTENTS = {
    "trend": (
        ("drug", "condition", "sponsor"),
        ("none", "status", "phase", "country"),
        {"analysis": "trend", "group_by": ["start_year"]},
        "how the number of trials changed per start year",
    ),
    "distribution": (
        ("drug", "condition"),
        ("none", "status", "years", "country"),
        {"analysis": "breakdown", "group_by": ["phase"]},
        "how trials are distributed across phases",
    ),
    "top_n": (
        ("drug", "condition"),
        ("none", "status", "phase", "years"),
        {"analysis": "breakdown", "group_by": ["lead_sponsor"], "top_n_set": True},
        "the top sponsors running these trials",
    ),
    "geographic": (
        ("drug", "condition"),
        ("none", "status", "phase"),
        {"analysis": "breakdown", "group_by": ["country"]},
        "which countries host the most trials",
    ),
    "comparison": (
        ("drug", "condition"),
        ("none", "status", "years"),
        {"analysis": "breakdown", "group_by": ["phase"]},
        "a comparison of the two named entities across trial phases",
    ),
    "network": (
        ("condition",),
        ("none", "status", "phase"),
        {"analysis": "network", "group_by": ["lead_sponsor", "drug"]},
        "a network linking sponsors to the drugs they test",
    ),
    "histogram": (
        ("drug", "condition"),
        ("none", "phase", "status"),
        {"analysis": "histogram", "measures": ["enrollment"]},
        "the distribution of trial enrollment sizes",
    ),
    "total": (
        ("drug", "condition", "sponsor"),
        ("status", "phase", "years", "country"),
        {"analysis": "total"},
        "a single count of matching trials",
    ),
    "out_of_scope": (
        ("drug",),
        ("none",),
        {"supported": False},
        "whether the drug works better than alternatives or what it costs",
    ),
}
ENTITY_FIELD = {"drug": "drug", "condition": "condition", "sponsor": "sponsor"}


def all_tuples() -> list[Tuple]:
    out = []
    for intent, (entities, filters, _, _) in INTENTS.items():
        for entity, flt, style in itertools.product(entities, filters, STYLES):
            out.append(Tuple(intent, entity, flt, style))
    return out


def sample_tuples(n: int, seed: int = 7) -> list[Tuple]:
    """Stratified: round-robin over intents, so every intent gets about n / len(INTENTS)."""
    rng = random.Random(seed)
    by_intent = {i: [t for t in all_tuples() if t.intent == i] for i in INTENTS}
    for ts in by_intent.values():
        rng.shuffle(ts)
    out: list[Tuple] = []
    for t in itertools.chain.from_iterable(itertools.zip_longest(*by_intent.values())):
        if t is not None and len(out) < n:
            out.append(t)
    return out


def expected_for(t: Tuple, entity: list[str]) -> dict:
    _, _, core, _ = INTENTS[t.intent]
    exp: dict = dict(core)
    if t.intent == "out_of_scope":
        return exp
    filters = dict(FILTERS[t.filter][0])
    if t.intent == "comparison":
        exp["comparison"] = {"field": ENTITY_FIELD[t.entity], "values": entity}
    else:
        filters[ENTITY_FIELD[t.entity]] = entity[0]
    exp["filters"] = filters
    return exp


def mentions_everything(question: str, exp: dict) -> bool:
    """Deterministic gate: the question names every entity and every filter constraint."""
    q = question.lower()
    needed = [v.lower() for v in (exp.get("comparison") or {}).get("values", [])]
    for key, value in exp.get("filters", {}).items():
        if key in ("drug", "condition", "sponsor", "country"):
            needed.append(value.lower())
        elif key == "start_year":
            needed.append(str(value))
        elif key == "status":
            needed.append("recruit")
        elif key == "phase":
            needed.append("3")
    return all(n in q for n in needed)


def _write_question(client: openai.OpenAI, t: Tuple, entity: list[str]) -> str:
    _, _, _, what = INTENTS[t.intent]
    constraint = {
        "none": "no extra constraint",
        "status": "only trials that are currently recruiting",
        "phase": "only phase 3 trials",
        "years": "only trials that started in 2018 or later",
        "country": "only trials with sites in Germany",
    }[t.filter]
    spec = {
        "asks_for": what,
        "about": entity,
        "constraint": constraint,
        "style": STYLES[t.style],
    }
    resp = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=[
            {
                "role": "system",
                "content": "You write one realistic question a user would ask a "
                "clinical-trials search tool. Mention every name in 'about' exactly as given. "
                'Express the constraint. Follow the style. Return JSON {"question": ...}.',
            },
            {"role": "user", "content": json.dumps(spec)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "q",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["question"],
                    "properties": {"question": {"type": "string"}},
                },
            },
        },
        temperature=1,
        reasoning_effort="none",
        max_completion_tokens=200,
    )
    return json.loads(resp.choices[0].message.content)["question"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    client = openai.OpenAI(api_key=LLMSettings().openai_api_key.get_secret_value())
    rng = random.Random(args.seed)
    cases, rejected = [], []
    for i, t in enumerate(sample_tuples(args.n, args.seed)):
        pool = ENTITIES[t.entity]
        entity = rng.sample(pool, 2) if t.intent == "comparison" else [rng.choice(pool)]
        exp = expected_for(t, entity)
        for _ in range(2):  # one retry if the question drops something
            q = _write_question(client, t, entity)
            if mentions_everything(q, exp):
                break
        else:
            rejected.append({"tuple": t._asdict(), "question": q})
            continue
        difficulty = (
            "hard"
            if t.style in ("indirect", "lowercase_typo")
            else ("medium" if t.filter != "none" or t.intent == "comparison" else "easy")
        )
        cases.append(
            {
                "id": f"syn_{i:03d}_{t.intent}",
                "query": q,
                "expect": exp,
                "intent": t.intent,
                "difficulty": difficulty,
                "source": "synthetic",
                "tuple": t._asdict(),
            }
        )
        print(f"{t.intent:12} {t.entity:9} {t.filter:7} {t.style:15} {q}")

    SYNTHETIC_FILE.parent.mkdir(exist_ok=True)
    SYNTHETIC_FILE.write_text(
        json.dumps(
            {
                "generator_model": GENERATOR_MODEL,
                "seed": args.seed,
                "cases": cases,
                "rejected": rejected,
            },
            indent=2,
        )
    )
    print(f"\n{len(cases)} cases, {len(rejected)} rejected -> {SYNTHETIC_FILE}")


if __name__ == "__main__":
    main()
