"""Evidence-driven routing — the Coordinator/Dispatcher pattern, in code.

This is the executable twin of ``references/orchestration/routing.md``. The
markdown explains the reasoning; this decides the roster.

The rule that matters: an agent spawns because the map found evidence for it.
A 300-line ERC-20 gets six agents, a full lending protocol gets twenty-five.
Cost tracks attack surface rather than tracking a constant somebody picked.
"""

from __future__ import annotations

from ..schemas import AgentSpec, Evidence, ModelTier, Roster, SystemMap

# Always-on. Six is the floor; below that it is not an audit.
CORE: list[tuple[str, str]] = [
    ("access-control", "core lane: permission model"),
    ("math-precision", "core lane: rounding and scale"),
    ("invariant", "core lane: properties that must hold"),
    ("execution-trace", "core lane: state-vs-call ordering"),
    ("first-principles", "core lane: intent vs implementation"),
    ("boundary", "core lane: edges, zeros, maxima"),
]

# evidence attribute -> agents it triggers
TRIGGERS: dict[str, list[str]] = {
    "has_oracle": ["oracle-expert", "signature-trust"],
    "has_lending": ["lending-expert", "economic-security"],
    "has_vault": ["yield-strategist", "numerical-gap"],
    "has_async_request_lifecycle": ["yield-strategist", "flow-gap"],
    "has_amm": ["economic-security", "asymmetry", "periphery"],
    "has_hooks": ["hook-ordering", "flow-gap"],
    "has_signatures": ["signature-trust", "signature-gap"],
    "has_delegatecall": ["proxy-upgrade"],
    "has_proxy_or_upgrade": ["proxy-upgrade"],
    "has_transient_storage": ["transient-storage"],
    "has_crosschain": ["crosschain-l2"],
    "has_account_abstraction": ["account-abstraction"],
    "has_tokenomics": ["tokenomics-analyst"],
    "has_governance_timelock": ["trust-gap"],
    "has_fee_math": ["asymmetry", "numerical-gap"],
    "has_fixed_point": ["numerical-gap"],
    "has_assembly": ["boundary", "proxy-upgrade"],
    "has_nft_identity": ["trust-gap", "erc-implementer"],
}

ROLE: dict[str, str] = {
    "numerical-gap": "gap", "flow-gap": "gap", "trust-gap": "gap", "signature-gap": "gap",
    "poc-writer": "proof", "formal-verifier": "proof", "fuzzer": "proof", "invariant-tester": "proof",
    "account-abstraction": "platform", "proxy-upgrade": "platform",
    "transient-storage": "platform", "crosschain-l2": "platform",
}

# The cap is a spend ceiling, not a target. Routing already sizes the roster to
# the attack surface: a plain token draws six agents at every budget, so the cap
# only bites on genuinely broad protocols - where a roster of twenty is the
# correct answer rather than an extravagant one.
BUDGETS: dict[str, dict] = {
    "quick":      {"cap": 8,    "verify_iterations": 1, "default_tier": ModelTier.triage},
    "standard":   {"cap": 22,   "verify_iterations": 2, "default_tier": ModelTier.deep},
    "deep":       {"cap": 28,   "verify_iterations": 2, "default_tier": ModelTier.deep},
    "exhaustive": {"cap": 9999, "verify_iterations": 3, "default_tier": ModelTier.deep},
}

# Drop order when the cap bites, lowest priority first. Core is never dropped.
#
# Platform specialists outrank domain specialists deliberately. A domain agent's
# lens overlaps heavily with the core lanes - economic-security and asymmetry
# cover much of the same ground, and access-control already reads every guard.
# Platform agents cover surfaces nothing else looks at: if proxy-upgrade is
# dropped, nobody checks storage collisions at all. Overlap is what makes an
# agent safe to drop; uniqueness is what makes it expensive to lose.
DROP_ORDER = ["proof", "gap", "domain", "platform", "lane"]

SLICING_SLOC_THRESHOLD = 2000

# These need the whole system in view; slicing them hides the cross-file bugs
# they exist to find.
NEEDS_FULL_SOURCE = {"first-principles", "invariant", "execution-trace", "access-control"}


def _gap_hunters(ev: Evidence, invariant_count: int, semi_trusted: int) -> list[tuple[str, str]]:
    """Gap-hunters find bugs at a seam, so both lenses must actually be present."""
    out: list[tuple[str, str]] = []
    numeric_lenses = sum([ev.has_fixed_point, ev.has_fee_math, ev.has_vault, ev.has_amm])
    if numeric_lenses >= 2:
        out.append(("numerical-gap", f"{numeric_lenses} numeric lenses present"))
    if ev.has_hooks:
        out.append(("flow-gap", "evidence.has_hooks"))
    if semi_trusted >= 1:
        out.append(("trust-gap", f"{semi_trusted} semi-trusted actor(s)"))
    if ev.has_signatures and (ev.has_oracle or ev.has_crosschain):
        out.append(("signature-gap", "signatures crossed with oracle/crosschain surface"))
    return out


def _proof_tooling(ev: Evidence, budget: str, smap: SystemMap) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if len(smap.invariants) >= 3:
        out.append(("invariant-tester", f"{len(smap.invariants)} candidate invariants"))
    if budget in {"deep", "exhaustive"} and smap.scope.compiles:
        out.append(("fuzzer", "budget>=deep and project compiles"))
    if budget == "exhaustive" or (budget == "deep" and (ev.has_lending or ev.has_vault)):
        out.append(("formal-verifier", "budget/domain warrants formal properties"))
    return out


def build_roster(smap: SystemMap, budget: str = "standard") -> Roster:
    cfg = BUDGETS.get(budget, BUDGETS["standard"])
    ev = smap.evidence
    semi_trusted = sum(1 for t in smap.trust_boundaries if t.trust == "semi-trusted")

    # agent_id -> list of reasons (an agent triggered three ways still runs once)
    reasons: dict[str, list[str]] = {}

    def add(agent_id: str, reason: str) -> None:
        reasons.setdefault(agent_id, []).append(reason)

    for agent_id, reason in CORE:
        add(agent_id, reason)

    for field, agents in TRIGGERS.items():
        if getattr(ev, field, False):
            for agent_id in agents:
                add(agent_id, f"evidence.{field}")

    if ev.erc_surfaces:
        add("erc-implementer", f"erc_surfaces={ev.erc_surfaces}")
    if ev.eip_surfaces:
        add("eip-expert", f"eip_surfaces={ev.eip_surfaces}")
    # EIP numbers are also platform triggers - a map that lists EIP7702 without
    # setting has_account_abstraction should still get the specialist.
    if any("7702" in s for s in ev.eip_surfaces):
        add("account-abstraction", "eip_surfaces contains EIP-7702")
    if any("1153" in s for s in ev.eip_surfaces):
        add("transient-storage", "eip_surfaces contains EIP-1153")

    for agent_id, reason in _gap_hunters(ev, len(smap.invariants), semi_trusted):
        add(agent_id, reason)
    for agent_id, reason in _proof_tooling(ev, budget, smap):
        add(agent_id, reason)

    core_ids = {a for a, _ in CORE}
    slicing = smap.scope.total_sloc > SLICING_SLOC_THRESHOLD

    specs: list[AgentSpec] = []
    for agent_id, why in reasons.items():
        role = "lane" if agent_id in core_ids else ROLE.get(agent_id, "domain")
        if not slicing or agent_id in NEEDS_FULL_SOURCE:
            slice_kind = "full"
        elif role in {"gap", "proof"}:
            slice_kind = "hot"
        else:
            slice_kind = "domain"
        specs.append(
            AgentSpec(
                agent_id=agent_id,
                role=role,
                spawn_reason="; ".join(why),
                model_tier=cfg["default_tier"] if role in {"lane", "domain", "platform"} else ModelTier.triage,
                context_slice=slice_kind,
            )
        )

    # ---- apply the cap ---------------------------------------------------
    skipped: list[dict] = []
    cap = cfg["cap"]
    if len(specs) > cap:
        keep: list[AgentSpec] = [s for s in specs if s.agent_id in core_ids]
        rest = [s for s in specs if s.agent_id not in core_ids]
        # Drop from the bottom of the priority order until we fit.
        rest.sort(key=lambda s: DROP_ORDER.index(s.role) if s.role in DROP_ORDER else 99)
        room = cap - len(keep)
        # Highest priority (latest in DROP_ORDER) survives, so walk in reverse.
        survivors = list(reversed(rest))[:room]
        dropped = [s for s in rest if s not in survivors]
        keep.extend(survivors)
        skipped = [{"agent_id": s.agent_id, "reason": f"budget={budget} cap={cap}"} for s in dropped]
        specs = keep

    # Deterministic order: core first, then by role, then alphabetically. A
    # roster that reshuffles between runs makes two manifests uncomparable.
    specs.sort(key=lambda s: (s.agent_id not in core_ids, s.role, s.agent_id))

    return Roster(budget=budget, slicing_enabled=slicing, agents=specs, skipped=skipped)


def verify_iterations(budget: str) -> int:
    return BUDGETS.get(budget, BUDGETS["standard"])["verify_iterations"]
