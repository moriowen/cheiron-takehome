"""HTTP API. Run: uv run uvicorn --factory trialviz.api:build_app --reload"""

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from trialviz.config import AppSettings, LLMSettings
from trialviz.ctgov.client import CTGovClient, CTGovError
from trialviz.llm.openai_planner import OpenAIPlanner
from trialviz.llm.planner import PlannerError
from trialviz.pipeline import Pipeline, PipelineError
from trialviz.schemas import QueryRequest, QueryResponse

FRONTEND = Path(__file__).parents[2] / "frontend"


def create_app(pipeline: Pipeline) -> FastAPI:
    app = FastAPI(
        title="Clinical trials query-to-visualization API",
        description="Turns a question about clinical trials into a visualization spec backed "
        "by ClinicalTrials.gov data, with citations for every datum.",
        version="1.0.0",
    )

    @app.post("/query", response_model=QueryResponse, response_model_exclude_none=False)
    def query(request: QueryRequest) -> QueryResponse:
        try:
            return pipeline.run(request)
        except PipelineError as e:
            raise HTTPException(422, str(e)) from e
        except PlannerError as e:
            raise HTTPException(502, f"Planner failed: {e}") from e
        except CTGovError as e:
            raise HTTPException(502, f"ClinicalTrials.gov failed: {e}") from e

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    if FRONTEND.is_dir():
        app.mount("/ui", StaticFiles(directory=FRONTEND, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse("/ui/")

    return app


def build_app() -> FastAPI:
    """Production app from settings and `.env`. Tests call `create_app` with fakes instead."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app_settings = AppSettings()
    planner = OpenAIPlanner(LLMSettings())
    client = CTGovClient(min_interval_s=app_settings.ctgov_min_interval_s)
    return create_app(
        Pipeline(
            planner,
            client,
            max_records=app_settings.max_records,
            cache_ttl_s=app_settings.cache_ttl_s,
        )
    )
