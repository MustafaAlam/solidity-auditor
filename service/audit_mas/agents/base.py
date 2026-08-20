"""Agent workers — provider-agnostic, with reliability baked in.

Design decisions worth knowing about:

* **The provider is an interface, not a vendor.** ``LLMProvider`` has one
  method. Anthropic, Vertex, an OpenAI-compatible endpoint and a deterministic
  stub all satisfy it, so the orchestration graph is testable with no network
  and no key.
* **Prompts come from the markdown, not from Python.** The specialty files under
  ``references/hacking-agents/`` are the single source of truth for what an
  agent looks for. Copying them into string literals here would create two
  versions that drift.
* **One retry, ever.** An agent that fails twice has a structural problem — a
  bad slice, an overflowing context, an impossible instruction — and a third
  attempt buys the same answer at the same price.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from ..schemas import AgentSpec, ModelTier

REFERENCES = pathlib.Path(__file__).resolve().parents[3] / "references"

MARKER_PATTERNS = {
    "feynman": re.compile(r"\[Feynman:"),
    "socratic": re.compile(r"\[Socratic:"),
    "inversion": re.compile(r"\[Inversion:"),
}

# Tier -> model. Override via config; unknown tiers fall back to `deep`.
DEFAULT_MODELS: dict[ModelTier, str] = {
    ModelTier.triage: "claude-haiku-4-5",
    ModelTier.deep: "claude-sonnet-4-5",
    ModelTier.verify: "claude-opus-4-1",
}


@dataclass
class Completion:
    """Text plus what it cost.

    v3.0.0 defined `cost.input_tokens` and `context_amplification` in the run
    manifest and never populated either, which meant the headline claim about
    routing cutting context amplification was unmeasured. Usage travels with the
    response so it cannot be forgotten - and it is per-call rather than an
    attribute on the provider, because concurrent agents share one provider
    instance and a mutable `last_usage` would race.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    def __str__(self) -> str:  # keeps `str(completion)` honest in logs
        return self.text


class LLMProvider(Protocol):
    """One method. That is the whole contract."""

    async def complete(self, *, system: str, prompt: str, model: str, max_tokens: int) -> Completion: ...


@dataclass
class AgentResult:
    agent_id: str
    records: list[dict] = field(default_factory=list)
    raw_text: str = ""
    markers: dict[str, int] = field(default_factory=dict)
    status: str = "ok"
    attempts: int = 0
    duration_s: float = 0.0
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


def load_reference(*parts: str) -> str:
    path = REFERENCES.joinpath(*parts)
    return path.read_text(encoding="utf-8") if path.exists() else ""


class MissingLensError(RuntimeError):
    """Raised when a routed agent has no specialty file.

    This used to be a silent empty string. An agent with no lens still returns
    findings, still reports ok, and still counts toward quorum - its silence on
    the bug it was meant to catch is indistinguishable from a clean result.
    Failing loudly at prompt-assembly time is the whole point.
    """


MIN_SPECIALTY_BYTES = 400


def load_specialty(agent_id: str) -> str:
    """Load an agent's lens, or raise. Never returns empty."""
    for candidate in (f"{agent_id}-agent.md", f"{agent_id}.md"):
        text = load_reference("hacking-agents", candidate)
        if len(text) >= MIN_SPECIALTY_BYTES:
            return text
        if text:
            raise MissingLensError(
                f"{candidate} exists but is {len(text)}B - below the {MIN_SPECIALTY_BYTES}B "
                f"floor, so '{agent_id}' would hunt with an effectively empty lens."
            )
    raise MissingLensError(
        f"No specialty file for '{agent_id}' in {REFERENCES / 'hacking-agents'}. "
        f"Expected {agent_id}-agent.md. Run scripts/check_lenses.py for the full list."
    )


def build_system_prompt(spec: AgentSpec) -> str:
    """Assemble the agent's system prompt from the same files the skill ships.

    Bundle order matters: how to think, then what to look for, then how to
    report. Reversing it puts output formatting in front of method and reliably
    produces well-formatted shallow work.

    Raises MissingLensError rather than assembling a prompt with no lens in it.
    """
    return "\n\n---\n\n".join(
        part for part in (
            load_reference("hacking-agents", "senior-auditor-sop.md"),
            load_specialty(spec.agent_id),
            load_reference("hacking-agents", "shared-rules.md"),
        ) if part
    )


def extract_records(text: str) -> list[dict]:
    """Pull JSON records out of a response.

    Models wrap JSON in prose and fences no matter how firmly you ask them not
    to. Being liberal here costs nothing — every record still has to pass the
    ledger's schema check, so leniency in parsing never becomes leniency in
    what reaches the report.
    """
    records: list[dict] = []

    for block in re.findall(r"```(?:json|jsonl)?\s*\n(.*?)```", text, re.DOTALL):
        records.extend(_parse_lines(block))

    if not records:
        records.extend(_parse_lines(text))

    # De-duplicate identical objects (fenced-and-repeated is common).
    seen: set[str] = set()
    unique: list[dict] = []
    for r in records:
        k = json.dumps(r, sort_keys=True)
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique


def _parse_lines(chunk: str) -> list[dict]:
    out: list[dict] = []
    for line in chunk.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "bug_class" in obj:
                out.append(obj)
    if out:
        return out
    # Fall back to a whole-chunk parse for pretty-printed objects or arrays.
    try:
        obj = json.loads(chunk)
    except json.JSONDecodeError:
        return []
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return [o for o in obj if isinstance(o, dict)]
    return []


def count_markers(text: str) -> dict[str, int]:
    return {name: len(pat.findall(text)) for name, pat in MARKER_PATTERNS.items()}


class HuntAgent:
    def __init__(
        self,
        spec: AgentSpec,
        provider: LLMProvider,
        *,
        models: dict[ModelTier, str] | None = None,
        timeout_s: float = 480.0,
        max_tokens: int = 16000,
    ):
        self.spec = spec
        self.provider = provider
        self.models = models or DEFAULT_MODELS
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def _model(self) -> str:
        return self.models.get(self.spec.model_tier, self.models[ModelTier.deep])

    def _prompt(self, bundle: str, system_map_json: str) -> str:
        return (
            f"OBJECTIVE\nFind exploitable defects through your specialty lens.\n\n"
            f"TASK BOUNDARIES\nSlice: {self.spec.context_slice} covering "
            f"{', '.join(self.spec.slice_files) or 'all in-scope files'}.\n"
            f"Stay in your lane. A finding expressible with a single different lens belongs to another agent.\n\n"
            f"SYSTEM MAP\n```json\n{system_map_json}\n```\n\n"
            f"SOURCE\n{bundle}\n\n"
            f"OUTPUT\nEmit JSON Lines only — one finding record per line, conforming to the schema in your "
            f"shared rules. Put mental-tool markers ([Feynman: ...], [Socratic: ...], [Inversion: ...]) in your "
            f"prose before the JSON, never inside it.\n"
            f"Never set `verification`, `judgment`, or `corroboration`.\n"
        )

    async def run(self, bundle: str, system_map_json: str) -> AgentResult:
        result = AgentResult(agent_id=self.spec.agent_id)
        started = time.monotonic()
        try:
            system = build_system_prompt(self.spec)
        except MissingLensError as exc:
            # Fail this agent loudly rather than letting it hunt with no lens.
            result.status, result.error = "failed", f"missing lens: {exc}"
            result.duration_s = round(time.monotonic() - started, 2)
            return result
        prompt = self._prompt(bundle, system_map_json)

        for attempt in (1, 2):  # one retry, ever
            result.attempts = attempt
            try:
                completion = await asyncio.wait_for(
                    self.provider.complete(
                        system=system, prompt=prompt, model=self._model(), max_tokens=self.max_tokens
                    ),
                    timeout=self.timeout_s,
                )
            except TimeoutError:
                result.status, result.error = "timeout", f"exceeded {self.timeout_s}s"
                continue
            except Exception as exc:  # provider errors are expected, not exceptional
                result.status, result.error = "failed", f"{type(exc).__name__}: {exc}"
                continue

            text = completion.text
            # Usage accumulates across attempts: a retry costs real money even
            # when the first attempt produced nothing usable.
            result.input_tokens += completion.input_tokens
            result.output_tokens += completion.output_tokens
            result.cached_input_tokens += completion.cached_input_tokens
            result.raw_text = text
            result.markers = count_markers(text)
            result.records = extract_records(text)
            if result.records:
                result.status = "ok" if attempt == 1 else "retried-ok"
                result.error = None
                break
            result.status, result.error = "invalid-output", "no parseable records in response"

        result.duration_s = round(time.monotonic() - started, 2)
        return result


class StubProvider:
    """Deterministic provider for tests and dry runs.

    The graph is worth exercising without spending money or needing a key. Every
    test in ``tests/`` runs against this.
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = ""):
        self.responses = responses or {}
        self.default = default
        self.calls: list[dict] = []

    async def complete(self, *, system: str, prompt: str, model: str, max_tokens: int) -> Completion:
        self.calls.append({"model": model, "prompt_len": len(prompt), "system_len": len(system)})
        text = self.default
        for key, response in self.responses.items():
            if key in system or key in prompt:
                text = response
                break
        # Rough but non-zero, so cost aggregation is exercised offline.
        return Completion(
            text=text,
            input_tokens=(len(system) + len(prompt)) // 4,
            output_tokens=len(text) // 4,
        )
