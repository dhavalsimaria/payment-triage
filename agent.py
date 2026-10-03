"""Classification backends for the payment-file triage pipeline.

Two independent ways to answer the same question - "what format is this payload,
and how confident are we?" - normalized into one `ClassificationResult` so
`routing_engine.py` never has to know which backend answered.

- `classify_jev`: calls TypeSafe AI's Jev model directly through the native
  `typesafe-sdk` `Choice` primitive. There is no OpenAI-compatible endpoint for
  Jev - it returns a typed judgment (chosen option + real confidence +
  probability distribution), not generated text, so no output parsing is needed.
- `classify_openai`: a PydanticAI `Agent` against `gpt-4o-mini`, the structural
  fallback used to compare a standard generative LLM's latency and output
  against Jev's. PydanticAI earns its keep here because OpenAI's output needs
  schema coercion; Jev's output is already typed.
"""

import os
import time
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from typesafe_sdk import AsyncTypeSafeClient, Choice, TypeSafeError

FormatType = Literal["pain_001", "mt101", "dtazv", "unknown"]

FORMAT_CRITERIA: dict[str, str] = {
    "pain_001": "ISO 20022 pain.001 XML Customer Credit Transfer Initiation message",
    "mt101": "SWIFT MT101 Request for Transfer message, fixed-field tag format (e.g. :20:, :50H:, :59:)",
    "dtazv": "German Bundesbank DTAZV flat-file foreign payment layout, fixed-width text records",
    "unknown": "Does not clearly match any of the above",
}

CLASSIFICATION_INSTRUCTIONS = (
    "Identify which payment file format the given payload is written in, "
    "based on its structural syntax (tags, field markers, XML elements, fixed-width layout)."
)


class ClassificationResult(BaseModel):
    format_type: FormatType
    confidence_score: float = Field(ge=0.0, le=1.0)
    model_used: str
    latency_ms: float
    raw_response: dict


class ClassificationError(Exception):
    """Raised when a classification backend can't produce a result (missing key, call failure)."""


class _FallbackClassification(BaseModel):
    format_type: FormatType
    confidence_score: float = Field(ge=0.0, le=1.0)


async def classify_jev(payload: str) -> ClassificationResult:
    """Classify payload via TypeSafe's Jev model using the native Choice primitive."""
    start = time.perf_counter()
    try:
        async with AsyncTypeSafeClient() as client:
            response = await client.system_one(
                state={"payload": payload},
                questions={
                    "format_type": Choice(
                        instructions=CLASSIFICATION_INSTRUCTIONS,
                        criteria=FORMAT_CRITERIA,
                    )
                },
                model="jev-latest",
            )
    except TypeSafeError as exc:
        raise ClassificationError(f"TypeSafe/Jev request failed: {exc}") from exc

    latency_ms = (time.perf_counter() - start) * 1000
    answer = response.choices["format_type"]
    return ClassificationResult(
        format_type=answer.choice,
        confidence_score=answer.confidence,
        model_used="typesafe/jev-latest",
        latency_ms=latency_ms,
        raw_response=response.model_dump(mode="json"),
    )


async def classify_openai(payload: str) -> ClassificationResult:
    """Classify payload via a PydanticAI Agent against OpenAI's gpt-4o-mini (fallback)."""
    if not os.getenv("OPENAI_API_KEY"):
        raise ClassificationError(
            "OPENAI_API_KEY is not set. Add it to your environment to use the OpenAI fallback."
        )

    start = time.perf_counter()
    try:
        agent = Agent(
            OpenAIChatModel("gpt-4o-mini"),
            output_type=_FallbackClassification,
            system_prompt=(
                f"{CLASSIFICATION_INSTRUCTIONS} Options: "
                + "; ".join(f"{key} = {desc}" for key, desc in FORMAT_CRITERIA.items())
                + ". Report your own calibrated confidence from 0 to 1."
            ),
        )
        result = await agent.run(payload)
    except Exception as exc:  # pydantic-ai surfaces provider/config errors as plain exceptions
        raise ClassificationError(f"OpenAI fallback request failed: {exc}") from exc

    latency_ms = (time.perf_counter() - start) * 1000
    return ClassificationResult(
        format_type=result.output.format_type,
        confidence_score=result.output.confidence_score,
        model_used="gpt-4o-mini",
        latency_ms=latency_ms,
        raw_response=result.output.model_dump(mode="json"),
    )
