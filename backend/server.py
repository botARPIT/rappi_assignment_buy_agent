"""FastAPI boundary for the buyer UI, agent, approval gate and evaluation report."""

import os
from pathlib import Path
from typing import Literal

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from backend.agent.agent import PurchasingAgent
from backend.mock.fixtures import get_cases
from backend.store import ConflictError, Store

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: StrictBool
    outcome: Literal["confirmed", "rejected", "partial"] = "confirmed"
    actor: str = Field(default="demo-buyer", min_length=1, max_length=80)

    @field_validator("approved")
    @classmethod
    def explicit_approval(cls, value):
        if value is not True:
            raise ValueError("Explicit buyer approval is required")
        return value


def create_app(db_path=None, provider=None):
    app = FastAPI(title="Rappi Purchasing Agent", version="0.2.0")
    store = Store(db_path or ROOT / ".data" / "purchasing-v2.sqlite3")
    agent = PurchasingAgent(store, provider=provider)
    app.state.store = store
    app.state.agent = agent

    @app.exception_handler(KeyError)
    async def missing_handler(request: Request, exc: KeyError):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.get("/health")
    def health():
        return {"status": "ok", "agent_provider": agent.provider, "framework": "langchain"}

    @app.get("/api/cases")
    def cases():
        return store.cases()

    @app.post("/api/agent/run")
    def run_agent(body: RunRequest):
        return agent.investigate(body.case_id)

    @app.post("/api/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: ApprovalRequest):
        return store.approve(draft_id, approved=body.approved, outcome=body.outcome, actor=body.actor)

    @app.post("/api/drafts/{draft_id}/reject")
    def reject(draft_id: str):
        return store.reject(draft_id)

    @app.post("/api/cases/{case_id}/reset")
    def reset(case_id: str):
        fixtures = get_cases()
        if case_id not in fixtures:
            raise KeyError(case_id)
        return store.update_case(fixtures[case_id])

    @app.get("/api/events")
    def events(case_id: str | None = None):
        return store.events(case_id)

    @app.get("/api/evals")
    def evaluations():
        from evaluations.run_eval import run

        return run(provider="scripted")

    @app.get("/")
    def home():
        return FileResponse(ROOT / "frontend" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
    return app


def main():
    uvicorn.run(create_app(), host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
