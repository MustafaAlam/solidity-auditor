"""A2A client — talk to remote agents as if they were local.

``RemoteHuntAgent`` matches ``HuntAgent``'s interface exactly, so the
orchestrator's phase graph is identical whether agents run in-process or as
separate Cloud Run services. Swapping transports should not be a rewrite.

Service discovery follows the codelab: one env var per agent holding its card
URL, e.g. ``AGENT_CARD_URL_ACCESS_CONTROL=https://...``.
"""

from __future__ import annotations

import os
import time

import httpx

from .agents.base import AgentResult
from .schemas import AgentSpec


def card_url_for(agent_id: str) -> str | None:
    return os.environ.get(f"AGENT_CARD_URL_{agent_id.upper().replace('-', '_')}")


def discover() -> dict[str, str]:
    """Every agent the environment knows how to reach."""
    prefix = "AGENT_CARD_URL_"
    return {
        key[len(prefix):].lower().replace("_", "-"): value
        for key, value in os.environ.items()
        if key.startswith(prefix) and value
    }


class RemoteHuntAgent:
    def __init__(self, spec: AgentSpec, base_url: str, *, timeout_s: float = 480.0, max_tokens: int = 16000):
        self.spec = spec
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    async def fetch_card(self) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.base_url}/.well-known/agent-card.json")
            resp.raise_for_status()
            return resp.json()

    async def run(self, bundle: str, system_map_json: str) -> AgentResult:
        result = AgentResult(agent_id=self.spec.agent_id)
        payload = {
            "run_id": os.environ.get("RUN_ID", "local"),
            "bundle": bundle,
            "system_map": system_map_json,
            "slice_files": self.spec.slice_files,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
        }
        started = time.monotonic()

        # One retry, matching the in-process policy. A worker that fails twice
        # has a structural problem, not a transient one.
        for attempt in (1, 2):
            result.attempts = attempt
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s + 30) as client:
                    resp = await client.post(f"{self.base_url}/tasks/hunt", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
            except (TimeoutError, httpx.HTTPError) as exc:
                result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
                continue

            result.records = data.get("records", [])
            result.markers = data.get("markers", {})
            result.status = data.get("status", "ok") if result.records else "invalid-output"
            result.error = data.get("error")
            if result.records:
                if attempt == 2:
                    result.status = "retried-ok"
                break

        result.duration_s = round(time.monotonic() - started, 2)
        return result


class RemoteVerifier:
    def __init__(self, base_url: str, *, timeout_s: float = 300.0, max_iterations: int = 2):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_iterations = max_iterations

    async def verify_one(self, finding, code_slice: str) -> dict:
        payload = {
            "run_id": os.environ.get("RUN_ID", "local"),
            "finding": finding.model_dump(mode="json", exclude_none=True),
            "code_slice": code_slice,
            "max_iterations": self.max_iterations,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s + 30) as client:
                resp = await client.post(f"{self.base_url}/tasks/verify", json=payload)
                resp.raise_for_status()
                return resp.json()["verification"]
        except (httpx.HTTPError, KeyError) as exc:
            # An unreachable verifier must not silently upgrade a finding to
            # "verified" — NOT-RUN carries a confidence penalty for this reason.
            return {
                "verifier_verdict": "NOT-RUN",
                "refutation": f"Verifier unreachable ({type(exc).__name__}); finding is unverified.",
                "counter_evidence": "",
                "iterations": 0,
                "residual_risk": "",
            }
