"""FastAPI ingestion gateway: triage endpoint, HITL queue endpoint, static dashboard."""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv()  # must run before agent.py's TYPESAFE_API_KEY / OPENAI_API_KEY lookups

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import ClassificationError, classify_jev, classify_openai
from routing_engine import HitlEntry, get_hitl_queue, route

MAX_PAYLOAD_CHARS = 4000

app = FastAPI(title="Payment Triage Gateway")

Backend = Literal["jev", "openai_fallback"]


class TriageRequest(BaseModel):
    payload: str
    backend: Backend


class TriageResponse(BaseModel):
    model_used: str
    latency_ms: float
    format_type: str
    confidence_score: float
    raw_response: dict
    routing_path: str
    message: str
    deprecation_warning: bool
    deprecation_message: str | None
    hitl: bool


@app.post("/api/triage", response_model=TriageResponse)
async def triage(request: TriageRequest) -> TriageResponse:
    payload = request.payload.strip()
    if not payload:
        raise HTTPException(status_code=400, detail="payload must not be empty")

    truncated = payload[:MAX_PAYLOAD_CHARS]

    try:
        if request.backend == "jev":
            classification = await classify_jev(truncated)
        else:
            classification = await classify_openai(truncated)
    except ClassificationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    decision = route(classification, truncated)

    return TriageResponse(
        model_used=classification.model_used,
        latency_ms=classification.latency_ms,
        format_type=classification.format_type,
        confidence_score=classification.confidence_score,
        raw_response=classification.raw_response,
        routing_path=decision.path,
        message=decision.message,
        deprecation_warning=decision.deprecation_warning,
        deprecation_message=decision.deprecation_message,
        hitl=decision.hitl,
    )


@app.get("/api/hitl", response_model=list[HitlEntry])
async def hitl_queue() -> list[HitlEntry]:
    return get_hitl_queue()


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
