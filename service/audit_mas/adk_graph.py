"""Optional Google ADK composition of the same graph.

The orchestrator in ``orchestrator.py`` is framework-free on purpose — it is the
supported path and it has no dependency beyond pydantic and httpx. This module
is for teams already standardized on ADK who want the audit expressed in ADK's
own primitives.

Mapping:

    ParallelAgent   -> HUNT      fan-out across the routed roster
    LoopAgent       -> VERIFY    generator/critic with max_iterations
    SequentialAgent -> the phase graph end to end

ADK's API has moved between releases. Import failures are handled explicitly
rather than at module scope so the rest of the package stays usable, and
``build_audit_graph`` raises a clear message instead of an ImportError traceback.
"""

from __future__ import annotations

from typing import Any

from .core.router import build_roster
from .schemas import SystemMap

ADK_AVAILABLE = True
try:  # pragma: no cover - depends on an optional extra
    from google.adk.agents import LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
except ImportError:  # pragma: no cover
    ADK_AVAILABLE = False
    LlmAgent = LoopAgent = ParallelAgent = SequentialAgent = None  # type: ignore[assignment]


def _require_adk() -> None:
    if not ADK_AVAILABLE:
        raise RuntimeError(
            "google-adk is not installed. Install the extra with:\n"
            "    pip install 'audit-mas[adk]'\n"
            "Or use the framework-free orchestrator in audit_mas.orchestrator, which is the supported path."
        )


def build_hunt_stage(system_map: SystemMap, budget: str, model: str) -> Any:
    """ParallelAgent over the routed roster.

    Each sub-agent writes to a distinct ``output_key``. Distinct keys are not
    cosmetic here: parallel ADK agents share session state, and a shared key is
    a race that silently loses one agent's findings.
    """
    _require_adk()
    from .agents.base import build_system_prompt

    roster = build_roster(system_map, budget)
    workers = [
        LlmAgent(
            name=spec.agent_id.replace("-", "_"),
            model=model,
            description=f"Solidity audit specialty: {spec.agent_id} ({spec.spawn_reason})",
            instruction=build_system_prompt(spec),
            output_key=f"findings_{spec.agent_id.replace('-', '_')}",
            # Specialists must not delegate. Uncontrolled transfer is how a fan-out
            # collapses into one agent doing everything badly.
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        for spec in roster.agents
    ]
    return ParallelAgent(name="hunt_fanout", sub_agents=workers)


def build_verify_stage(model: str, max_iterations: int = 2) -> Any:
    """LoopAgent implementing generator/critic with a hard ceiling.

    The escalation checker exits the loop the moment a terminal verdict lands, so
    the ceiling is a backstop rather than the normal path.
    """
    _require_adk()
    from .agents.base import load_reference

    critic = LlmAgent(
        name="adversarial_verifier",
        model=model,
        description="Attacks a candidate finding and tries to refute it.",
        instruction=load_reference("hacking-agents", "adversarial-verifier-agent.md"),
        output_key="verification",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    checker = LlmAgent(
        name="escalation_checker",
        model=model,
        description="Exits the loop when the verifier reaches a terminal verdict.",
        instruction=(
            "Read {verification}. If verifier_verdict is CONFIRMED, REFUTED, or UNREACHABLE-CODE, "
            "escalate to end the loop. If it is WEAKENED, allow exactly one more iteration for a "
            "narrowing repair. Never allow a repair that introduces a new attack path — that is a "
            "different finding, not a rescue of this one."
        ),
        output_key="escalation",
    )
    return LoopAgent(name="verify_loop", sub_agents=[critic, checker], max_iterations=max_iterations)


def build_audit_graph(system_map: SystemMap, budget: str = "standard", model: str = "gemini-2.5-pro") -> Any:
    """MAP -> HUNT -> VERIFY -> REDUCE as one SequentialAgent."""
    _require_adk()
    from .agents.base import load_reference
    from .core.router import verify_iterations

    mapper = LlmAgent(
        name="system_mapper",
        model=model,
        description="Builds the SystemMapArtifact that drives routing and coverage.",
        instruction=(
            "Produce a SystemMapArtifact conforming to schemas/system-map.schema.json. "
            "The `evidence` block drives which specialists are spawned and "
            "`hot_functions[].required_axes` is the denominator of the coverage gate — "
            "both must be filled from the code, not guessed."
        ),
        output_key="system_map",
    )
    reducer = LlmAgent(
        name="reducer",
        model=model,
        description="Adjudicates ambiguous merges; all mechanical reduction is done in code.",
        instruction=(
            "Deterministic reduction has already run in audit_mas.core.reduction. "
            "Your only task is the merge_queue: for each entry, decide whether two differently-tagged "
            "bug classes in the same function are one defect or two. Split when in doubt.\n\n"
            + load_reference("orchestration", "judging.md")
        ),
        output_key="reduced",
    )
    return SequentialAgent(
        name="solidity_audit_mas",
        sub_agents=[
            mapper,
            build_hunt_stage(system_map, budget, model),
            build_verify_stage(model, verify_iterations(budget)),
            reducer,
        ],
    )
