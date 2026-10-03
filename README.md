# Payment Triage Gateway

An ingestion gateway that classifies raw payment file payloads (ISO 20022 pain.001,
SWIFT MT101, German DTAZV) and deterministically routes them, built to evaluate
[TypeSafe AI](https://typesafe.ai)'s **Jev** model's speed and classification quality
against an OpenAI (`gpt-4o-mini`) fallback on the same task.

## How it works

1. A payload (up to 4,000 characters) is sent to one of two classification backends:
   - **Jev** — called directly through the native `typesafe-sdk`, using a `Choice`
     primitive over the four format options. Jev returns a typed judgment (chosen
     format + real confidence + full probability distribution) rather than generated
     text, so no parsing is needed.
   - **OpenAI fallback** — a [PydanticAI](https://ai.pydantic.dev) `Agent` against
     `gpt-4o-mini`, used because a generative model's output genuinely needs
     PydanticAI's schema coercion (Jev's output is already typed at the source).
2. Both backends return the same `ClassificationResult` shape (format, confidence,
   model name, latency), so the routing logic never needs to know which one answered.
3. `routing_engine.py` applies fixed, deterministic rules — no model is consulted here:
   - `pain_001` with confidence ≥ 0.85 → simulated ISO 20022 XSD validation success.
   - `mt101` or `dtazv` (any confidence) → simulated legacy engine + an EU 2026
     hard-decommission deprecation warning.
   - Everything else (`unknown`, or any format below the confidence threshold,
     including a low-confidence `pain_001`) → appended to an in-memory Human Review
     Dead-Letter Queue, UI shows "Awaiting Human Triage".

See [agent.py](agent.py), [routing_engine.py](routing_engine.py), and [main.py](main.py)
for the implementation; `static/index.html` is the operational dashboard.

## Project layout

```
main.py             FastAPI app: /api/triage, /api/hitl, static file mount
agent.py             classify_jev() (typesafe-sdk) / classify_openai() (PydanticAI)
routing_engine.py    Deterministic routing rules + in-memory HITL queue
static/index.html    Tailwind + vanilla JS operational dashboard (no build step)
```

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env
```

Fill in `.env`:

| Variable | Required for | Notes |
|---|---|---|
| `TYPESAFE_API_KEY` | Jev backend | Get one at https://console.typesafe.ai/ |
| `OPENAI_API_KEY` | OpenAI fallback backend | Standard OpenAI API key |

Either key can be omitted — the dashboard still loads and the backend missing its key
returns a clear `503` instead of crashing the server or silently using the other backend.

## Running

```bash
uv run uvicorn main:app --reload --port 8420
```

Open http://localhost:8420. Paste a payload (or use one of the three mock-payload
buttons), pick a backend, and click **Run Triage**. The dashboard shows the API
latency in milliseconds, the raw JSON response from the classification backend,
the deterministic execution path taken, and — where applicable — an amber EU 2026
deprecation banner or a red Human-in-the-Loop banner. Entries routed to HITL appear
in the Dead-Letter Queue table at the bottom.

## API

### `POST /api/triage`

```json
{ "payload": "<raw file contents>", "backend": "jev" }
```

`backend` is `"jev"` or `"openai_fallback"`. Response:

```json
{
  "model_used": "typesafe/jev-latest",
  "latency_ms": 574.3,
  "format_type": "dtazv",
  "confidence_score": 0.99,
  "raw_response": { "...": "raw backend response" },
  "routing_path": "legacy",
  "message": "Legacy format 'dtazv' accepted by the simulated legacy processing engine.",
  "deprecation_warning": true,
  "deprecation_message": "DEPRECATION WARNING: ...",
  "hitl": false
}
```

Returns `400` for an empty payload, `503` if the selected backend's API key is
missing or the request to it fails.

### `GET /api/hitl`

Returns the current contents of the in-memory Human Review Dead-Letter Queue.

## Notes

- `migration_warning_required` is not asked of either model — it's a deterministic
  rule in `routing_engine.py` (`mt101`/`dtazv` ⇒ `True`), since it's a fixed
  regulatory fact rather than something needing semantic judgment.
- The HITL queue is in-memory only (resets on restart) — this is an evaluation/ops
  tool, not a system of record.
