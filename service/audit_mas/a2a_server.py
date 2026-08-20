"""A2A server — each agent as an independently deployable microservice.

This is the codelab's shape: agents talk over HTTP rather than function calls,
each publishes an agent card at ``/.well-known/agent-card.json``, and each
deploys, scales and fails on its own.

Run one process per agent locally:

    uvicorn audit_mas.a2a_server:app --port 8001 --env AGENT_ID=access-control
    uvicorn audit_mas.a2a_server:app --port 8002 --env AGENT_ID=oracle-expert

or containerize the same image once and vary ``AGENT_ID`` per Cloud Run service.

The orchestrator reaches these through ``a2a_client.py``, which is a drop-in
replacement for the in-process ``HuntAgent`` — the phase graph does not know or
care which transport it is using.
"""

from __future__ import annotations

import os
import pathlib
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agents.base import HuntAgent, build_system_prompt
from .agents.verifier import AdversarialVerifier
from .schemas import AgentSpec, Finding, ModelTier

AGENT_ID = os.environ.get("AGENT_ID", "access-control")
AGENT_ROLE = os.environ.get("AGENT_ROLE", "lane")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8001")
MODEL_TIER = ModelTier(os.environ.get("MODEL_TIER", "deep"))

app = FastAPI(title=f"solidity-auditor::{AGENT_ID}", version="3.0.0")


def _provider():
    """Resolve the LLM provider from the environment.

    Falls back to a stub when no key is configured, so `docker run` and CI both
    work without credentials and the health check means something.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        from .providers import AnthropicProvider

        return AnthropicProvider()
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") == "true":
        from .providers import VertexProvider

        return VertexProvider()
    from .agents.base import StubProvider

    return StubProvider(default="")


# ---------------------------------------------------------------------------
# agent card
# ---------------------------------------------------------------------------
def _specialty_summary() -> str:
    text = build_system_prompt(AgentSpec(agent_id=AGENT_ID, role=AGENT_ROLE, spawn_reason="card"))
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and len(line) > 40:
            return line[:300]
    return f"Solidity audit specialty: {AGENT_ID}"


@app.get("/.well-known/agent-card.json")
def agent_card() -> dict[str, Any]:
    return {
        "protocolVersion": "0.2",
        "name": AGENT_ID,
        "description": _specialty_summary(),
        "url": PUBLIC_URL,
        "version": "3.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": f"hunt:{AGENT_ID}",
                "name": f"Hunt ({AGENT_ID})",
                "description": _specialty_summary(),
                "tags": ["solidity", "security", AGENT_ROLE],
                # Advertising the schema is what lets an orchestrator validate a
                # remote agent's output without trusting the remote agent.
                "outputSchema": "https://solidity-auditor/schemas/finding.schema.json",
            }
        ],
    }


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "agent_id": AGENT_ID, "tier": MODEL_TIER.value}


# ---------------------------------------------------------------------------
# task endpoints
# ---------------------------------------------------------------------------
class HuntRequest(BaseModel):
    run_id: str
    bundle: str = Field(description="Source slice plus any extra context.")
    system_map: str = Field(default="{}", description="SystemMapArtifact as JSON text.")
    slice_files: list[str] = Field(default_factory=list)
    max_tokens: int = 16000
    timeout_s: float = 480.0


class HuntResponse(BaseModel):
    run_id: str
    agent_id: str
    status: str
    records: list[dict]
    invalid_records: list[dict] = Field(default_factory=list)
    markers: dict[str, int] = Field(default_factory=dict)
    attempts: int = 0
    duration_s: float = 0.0
    error: str | None = None


@app.post("/tasks/hunt", response_model=HuntResponse)
async def hunt(req: HuntRequest) -> HuntResponse:
    spec = AgentSpec(
        agent_id=AGENT_ID,
        role=AGENT_ROLE,
        spawn_reason="a2a request",
        model_tier=MODEL_TIER,
        slice_files=req.slice_files,
    )
    agent = HuntAgent(spec, _provider(), timeout_s=req.timeout_s, max_tokens=req.max_tokens)
    result = await agent.run(req.bundle, req.system_map)

    # Validate here, at the service boundary. The orchestrator should never have
    # to trust a remote worker's idea of the schema.
    valid, invalid = [], []
    for rec in result.records:
        try:
            valid.append(Finding.model_validate(rec).model_dump(mode="json", exclude_none=True))
        except Exception as exc:
            invalid.append({"record": rec, "error": str(exc)})

    return HuntResponse(
        run_id=req.run_id,
        agent_id=AGENT_ID,
        status=result.status,
        records=valid,
        invalid_records=invalid,
        markers=result.markers,
        attempts=result.attempts,
        duration_s=result.duration_s,
        error=result.error,
    )


class VerifyRequest(BaseModel):
    run_id: str
    finding: dict
    code_slice: str
    max_iterations: int = 2


@app.post("/tasks/verify")
async def verify(req: VerifyRequest) -> dict[str, Any]:
    if AGENT_ROLE != "verifier":
        raise HTTPException(status_code=400, detail=f"{AGENT_ID} is not a verifier (role={AGENT_ROLE})")
    try:
        finding = Finding.model_validate(req.finding)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid finding: {exc}") from exc

    started = time.monotonic()
    verifier = AdversarialVerifier(_provider(), max_iterations=req.max_iterations)
    verification = await verifier.verify_one(finding, req.code_slice)
    return {
        "run_id": req.run_id,
        "group_key": finding.group_key,
        "verification": verification.model_dump(mode="json"),
        "duration_s": round(time.monotonic() - started, 2),
    }


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "solidity-auditor",
        "agent_id": AGENT_ID,
        "role": AGENT_ROLE,
        "version": "3.0.0",
        "card": "/.well-known/agent-card.json",
        "references_found": (pathlib.Path(__file__).resolve().parents[2] / "references").exists(),
    }
