"""Deterministic routing logic and the in-memory Human Review Dead-Letter Queue.

Pure functions over a `ClassificationResult` (see agent.py). No model is ever
consulted here - every decision below is a fixed business rule, which is the
whole point of separating judgment (the agent) from execution (this module).
"""

import itertools
from datetime import datetime, timezone

from pydantic import BaseModel

from agent import ClassificationResult

CONFIDENCE_THRESHOLD = 0.85
DEPRECATION_WARNING = (
    "DEPRECATION WARNING: MT101 and DTAZV are being hard-decommissioned in the "
    "EU in November 2026. This file must be migrated to ISO 20022 pain.001 "
    "before the cutover."
)

_hitl_id_counter = itertools.count(1)


class HitlEntry(BaseModel):
    id: int
    received_at: datetime
    model_used: str
    format_type: str
    confidence_score: float
    payload_excerpt: str


class RoutingDecision(BaseModel):
    path: str  # "iso20022" | "legacy" | "hitl"
    message: str
    deprecation_warning: bool = False
    deprecation_message: str | None = None
    hitl: bool = False


HITL_QUEUE: list[HitlEntry] = []


def simulate_xsd_validation() -> str:
    """Stand in for a real ISO 20022 XSD schema check (out of scope for this triage demo)."""
    return "Success: File valid against ISO 20022 schemas"


def _enqueue_hitl(classification: ClassificationResult, payload: str) -> HitlEntry:
    entry = HitlEntry(
        id=next(_hitl_id_counter),
        received_at=datetime.now(timezone.utc),
        model_used=classification.model_used,
        format_type=classification.format_type,
        confidence_score=classification.confidence_score,
        payload_excerpt=payload[:200],
    )
    HITL_QUEUE.append(entry)
    return entry


def route(classification: ClassificationResult, payload: str) -> RoutingDecision:
    """Apply the fixed ISO20022 / legacy / HITL rules to a classification result."""
    if (
        classification.format_type == "pain_001"
        and classification.confidence_score >= CONFIDENCE_THRESHOLD
    ):
        return RoutingDecision(path="iso20022", message=simulate_xsd_validation())

    if classification.format_type in ("mt101", "dtazv"):
        return RoutingDecision(
            path="legacy",
            message=(
                f"Legacy format '{classification.format_type}' accepted by the "
                "simulated legacy processing engine."
            ),
            deprecation_warning=True,
            deprecation_message=DEPRECATION_WARNING,
        )

    _enqueue_hitl(classification, payload)
    return RoutingDecision(path="hitl", message="Awaiting Human Triage", hitl=True)


def get_hitl_queue() -> list[HitlEntry]:
    return list(HITL_QUEUE)
