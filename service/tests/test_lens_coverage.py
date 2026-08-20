"""Lens coverage — the test that would have caught the blind-agent bug.

The v3.0.0 bundle shipped 5 specialty files for 30 routable agents. Nothing
failed. `build_system_prompt` returned an empty string, the empty section was
filtered out of the bundle, and 25 agents hunted with the SOP, the shared rules,
and no lens at all — returning confident generic findings while the manifest
recorded `status: ok`.

No test caught it because no test asserted the relationship between "what the
router can spawn" and "what exists on disk". These do.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from audit_mas.agents.base import (
    MIN_SPECIALTY_BYTES,
    HuntAgent,
    MissingLensError,
    StubProvider,
    build_system_prompt,
    load_specialty,
)
from audit_mas.core.router import CORE, ROLE, TRIGGERS, build_roster
from audit_mas.orchestrator import Orchestrator
from audit_mas.schemas import AgentSpec, Evidence, Scope, SystemMap

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS_DIR = ROOT / "references" / "hacking-agents"
MANIFEST = AGENTS_DIR / "MANIFEST.json"


def routable_agent_ids() -> set[str]:
    return (
        {a for a, _ in CORE}
        | {a for agents in TRIGGERS.values() for a in agents}
        | set(ROLE)
        | {"erc-implementer", "eip-expert", "adversarial-verifier"}
    )


# ---------------------------------------------------------------------------
# the manifest is the contract
# ---------------------------------------------------------------------------
def test_manifest_exists():
    assert MANIFEST.exists(), "MANIFEST.json is what makes lens coverage checkable at all"


def test_every_routable_agent_is_declared():
    """The router must never be able to spawn something the manifest doesn't know."""
    declared = {lens["agent_id"] for lens in json.loads(MANIFEST.read_text())["lenses"]}
    undeclared = routable_agent_ids() - declared
    assert not undeclared, (
        f"router can spawn {sorted(undeclared)} but MANIFEST.json does not declare them, "
        "so check_lenses.py would not notice they were missing"
    )


def test_every_declared_lens_has_a_real_file():
    """The headline check.

    This was an xfail while the 25 v2.7 lenses were missing from the bundle.
    They are in now, so it is a hard assertion — if a lens is ever dropped from
    a release again, this is the test that fails.
    """
    missing = []
    for lens in json.loads(MANIFEST.read_text())["lenses"]:
        path = AGENTS_DIR / lens["file"]
        if not path.exists() or len(path.read_text()) < MIN_SPECIALTY_BYTES:
            missing.append(lens["agent_id"])
    assert not missing, f"{len(missing)} agent(s) would hunt with no lens: {sorted(missing)}"


# ---------------------------------------------------------------------------
# a missing lens must be loud, not empty
# ---------------------------------------------------------------------------
def test_missing_lens_raises_instead_of_returning_empty():
    with pytest.raises(MissingLensError, match="No specialty file"):
        load_specialty("definitely-not-a-real-agent")


def test_build_system_prompt_refuses_to_assemble_a_blind_prompt():
    spec = AgentSpec(agent_id="definitely-not-a-real-agent", role="lane", spawn_reason="test")
    with pytest.raises(MissingLensError):
        build_system_prompt(spec)


def test_stub_lens_is_treated_as_missing(tmp_path, monkeypatch):
    """A file that exists but is a placeholder is still a blind agent."""
    import audit_mas.agents.base as base

    fake = tmp_path / "hacking-agents"
    fake.mkdir()
    (fake / "stub-lens-agent.md").write_text("# Stub\n\nTODO.\n")
    monkeypatch.setattr(base, "REFERENCES", tmp_path)
    with pytest.raises(MissingLensError, match="below the"):
        base.load_specialty("stub-lens")


def test_blind_agent_fails_itself_rather_than_hunting():
    spec = AgentSpec(agent_id="definitely-not-a-real-agent", role="lane", spawn_reason="test")
    agent = HuntAgent(spec, StubProvider(default='{"bug_class": "x"}'))
    result = asyncio.run(agent.run("contract C {}", "{}"))
    assert result.status == "failed"
    assert "missing lens" in (result.error or "")
    assert result.records == [], "a lensless agent must not contribute findings"


# ---------------------------------------------------------------------------
# the orchestrator refuses to run a blind core
# ---------------------------------------------------------------------------
def test_orchestrator_detects_blind_agents_at_route(tmp_path):
    smap = SystemMap(scope=Scope(total_sloc=500), evidence=Evidence())
    orch = Orchestrator(StubProvider(default=""), tmp_path / "run", budget="quick")
    orch.route(smap)
    core_ids = {a for a, _ in CORE}
    on_disk = {p.name.replace("-agent.md", "") for p in AGENTS_DIR.glob("*-agent.md")}
    assert set(orch.blind_agents) == core_ids - on_disk


def test_run_aborts_when_a_core_lane_is_blind(tmp_path):
    """Not a degraded audit — a different, much smaller audit in the same template."""
    smap = SystemMap(scope=Scope(total_sloc=500), evidence=Evidence())
    orch = Orchestrator(StubProvider(default=""), tmp_path / "run", budget="quick")
    result = asyncio.run(orch.run(smap, {"full": "contract C {}"}, {}))

    core_ids = {a for a, _ in CORE}
    on_disk = {p.name.replace("-agent.md", "") for p in AGENTS_DIR.glob("*-agent.md")}
    if core_ids - on_disk:
        assert result.get("aborted"), "a blind core lane must stop the run"
        assert "no specialty file" in result["reason"]
    else:
        # Every lens is present, so the blind-lane abort must not fire. The run
        # can still abort on quorum (the stub provider returns nothing usable) -
        # what matters is that it aborts for the honest reason.
        assert "no specialty file" not in result.get("reason", "")


def test_roster_only_spawns_agents_the_manifest_declares():
    """Belt and braces: exercise a maximal roster, not just the default one."""
    rich = SystemMap(
        scope=Scope(total_sloc=9000),
        evidence=Evidence(
            has_oracle=True, has_lending=True, has_vault=True, has_hooks=True, has_amm=True,
            has_signatures=True, has_proxy_or_upgrade=True, has_transient_storage=True,
            has_crosschain=True, has_account_abstraction=True, has_fee_math=True,
            has_fixed_point=True, has_tokenomics=True, has_governance_timelock=True,
            has_nft_identity=True, erc_surfaces=["ERC4626"], eip_surfaces=["EIP712"],
        ),
    )
    declared = {lens["agent_id"] for lens in json.loads(MANIFEST.read_text())["lenses"]}
    for budget in ("quick", "standard", "deep", "exhaustive"):
        for spec in build_roster(rich, budget).agents:
            assert spec.agent_id in declared, f"{spec.agent_id} spawnable at {budget} but undeclared"
