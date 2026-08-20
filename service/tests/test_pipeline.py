"""End-to-end and unit tests. No network, no API key, no cost.

The point of these is not coverage for its own sake — it is that the guarantees
the docs claim (function isolation, fix preservation, quorum, one retry) are
asserted somewhere rather than merely described.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from conftest import da, finding

from audit_mas.agents.base import HuntAgent, StubProvider, count_markers, extract_records
from audit_mas.agents.verifier import AdversarialVerifier
from audit_mas.core.ledger import Ledger
from audit_mas.core.reduction import compute_confidence, reduce_findings, severity_cap
from audit_mas.core.router import build_roster
from audit_mas.ingest import build_bundle, build_system_map
from audit_mas.orchestrator import Orchestrator
from audit_mas.schemas import AgentSpec, Evidence, Finding, HotFunction, Scope, SystemMap, VerifierVerdict


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
def test_group_key_is_derived_not_trusted():
    rec = finding()
    rec["group_key"] = "totally|wrong|key"
    assert Finding.model_validate(rec).group_key == "Vault|withdraw|reentrancy"


def test_finding_without_proof_is_rejected():
    rec = finding()
    del rec["proof"]
    with pytest.raises(Exception, match="proof"):
        Finding.model_validate(rec)


def test_medium_without_poc_is_rejected():
    rec = finding(severity="medium")
    del rec["poc"]
    with pytest.raises(Exception, match="poc"):
        Finding.model_validate(rec)


def test_da_blocker_without_bypass_cannot_claim_survives():
    rec = finding()
    rec["devils_advocate"] = da(block="access")
    rec["devils_advocate"]["verdict"] = "survives"
    with pytest.raises(Exception, match="bypass"):
        Finding.model_validate(rec)


def test_da_blocker_with_named_bypass_is_accepted():
    rec = finding()
    rec["devils_advocate"] = da(block="access", bypass="initialize() is unguarded, so the role is acquirable")
    assert Finding.model_validate(rec).devils_advocate.verdict == "survives"


# ---------------------------------------------------------------------------
# ingest + routing
# ---------------------------------------------------------------------------
def test_ingest_excludes_tests_and_finds_evidence(vulnerable_source):
    smap, sources = build_system_map(vulnerable_source)
    assert not any("test" in f for f in smap.scope.files)
    assert smap.evidence.has_oracle
    assert smap.evidence.has_vault or smap.evidence.has_fee_math
    assert smap.hot_functions, "static ranking produced no hot functions"


def test_routing_scales_with_evidence():
    bare = SystemMap(scope=Scope(total_sloc=200), evidence=Evidence())
    rich = SystemMap(
        scope=Scope(total_sloc=9000),
        evidence=Evidence(
            has_oracle=True, has_lending=True, has_vault=True, has_hooks=True,
            has_signatures=True, has_proxy_or_upgrade=True, has_transient_storage=True,
            has_crosschain=True, has_account_abstraction=True, has_fee_math=True,
            has_fixed_point=True, erc_surfaces=["ERC4626"], eip_surfaces=["EIP712"],
        ),
    )
    small, large = build_roster(bare, "standard"), build_roster(rich, "standard")
    assert len(small.agents) == 6, "core floor should be exactly the six always-on lanes"
    assert len(large.agents) > len(small.agents)
    assert {"oracle-expert", "proxy-upgrade", "transient-storage",
            "crosschain-l2", "account-abstraction"} <= {a.agent_id for a in large.agents}


def test_budget_cap_never_drops_core():
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
    roster = build_roster(rich, "quick")
    ids = {a.agent_id for a in roster.agents}
    assert len(roster.agents) <= 8
    assert {"access-control", "math-precision", "invariant",
            "execution-trace", "first-principles", "boundary"} <= ids
    assert roster.skipped, "cap should have recorded what it dropped"


def test_every_agent_records_why_it_spawned():
    smap = SystemMap(scope=Scope(total_sloc=5000), evidence=Evidence(has_oracle=True, has_lending=True))
    for spec in build_roster(smap, "standard").agents:
        assert spec.spawn_reason, f"{spec.agent_id} has no spawn_reason"


def test_slicing_only_above_threshold():
    assert not build_roster(SystemMap(scope=Scope(total_sloc=1999)), "standard").slicing_enabled
    assert build_roster(SystemMap(scope=Scope(total_sloc=2001)), "standard").slicing_enabled


def test_full_source_agents_never_get_sliced():
    smap = SystemMap(scope=Scope(total_sloc=9000), evidence=Evidence(has_oracle=True))
    for spec in build_roster(smap, "standard").agents:
        if spec.agent_id in {"first-principles", "invariant", "execution-trace", "access-control"}:
            assert spec.context_slice == "full"


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------
def test_ledger_quarantines_rather_than_dropping(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    good, bad = finding(), finding()
    del bad["proof"]

    accepted, rejected, _ = ledger.append_many("access-control", [good, bad])
    assert (accepted, rejected) == (1, 1)
    assert len(ledger.read_all()) == 1
    assert len(ledger.quarantined()) == 1, "a rejected record must survive as evidence"


def test_ledger_isolates_agents(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    ledger.append("access-control", finding())
    ledger.append("oracle-expert", finding(agent_id="oracle-expert", contract="PriceFeed",
                                           function="latestPrice", bug_class="stale-oracle"))
    assert ledger.agents_with_output() == {"access-control", "oracle-expert"}
    assert len(ledger.read_all()) == 2


# ---------------------------------------------------------------------------
# reduction
# ---------------------------------------------------------------------------
def _as_findings(records: list[dict]) -> list[Finding]:
    return [Finding.model_validate(r) for r in records]


def test_synonyms_merge_and_corroboration_counts(sample_records):
    result = reduce_findings(_as_findings(sample_records))
    reentrancy = [r for r in result["reduced"] if r["bug_class"] in
                  {"reentrancy", "cross-function-reentrancy"}]
    assert len(reentrancy) == 1, "synonymous bug classes in one function must merge"
    assert reentrancy[0]["agent_count"] == 2
    assert len(reentrancy[0]["mechanisms"]) >= 1


def test_function_isolation_is_structural(sample_records):
    """Different functions are different bugs. Always."""
    result = reduce_findings(_as_findings(sample_records))
    keys = {(r["contract"], r["function"]) for r in result["reduced"]}
    assert ("Vault", "withdraw") in keys
    assert ("Vault", "preview") in keys, "a lead in another function must not be absorbed"


def test_coexisting_defects_both_survive(sample_records):
    result = reduce_findings(_as_findings(sample_records))
    withdraw = [r for r in result["reduced"] if r["function"] == "withdraw"]
    assert len(withdraw) == 2, "reentrancy and rounding coexist and must both be reported"
    assert result["coexisting"], "coexistence must be flagged for the report writer"


def test_distinct_fixes_are_preserved():
    records = [
        finding(agent_id="a", fix={"label": "restrict", "summary": "Add onlyOwner.",
                                   "add_lines": ["require(msg.sender == owner)"]}),
        finding(agent_id="b", fix={"label": "validate", "summary": "Check the amount.",
                                   "add_lines": ["require(amount <= cap)"]}),
    ]
    reduced = reduce_findings(_as_findings(records))["reduced"][0]
    assert len(reduced["fixes"]) == 2, "distinct fixes must survive as separate options"


def test_identical_fixes_collapse():
    records = [finding(agent_id="a"), finding(agent_id="b")]
    assert len(reduce_findings(_as_findings(records))["reduced"][0]["fixes"]) == 1


def test_completeness_is_accounted(sample_records):
    result = reduce_findings(_as_findings(sample_records))
    assert result["completeness"]["ok"]
    assert result["completeness"]["dropped"] == []


# ---------------------------------------------------------------------------
# confidence
# ---------------------------------------------------------------------------
def test_confidence_rewards_execution_over_argument():
    sketch = Finding.model_validate(finding())
    passing = Finding.model_validate(
        finding(poc={"status": "passing", "framework": "foundry",
                     "code": "function testExploit() public {}", "command": "forge test"})
    )
    assert compute_confidence(passing, 1) - compute_confidence(sketch, 1) == 20


def test_confidence_penalizes_unverified_and_unbypassed_blockers():
    base = Finding.model_validate(finding())
    blocked = Finding.model_validate(
        finding(devils_advocate=da(block="economic") | {"verdict": "demote-to-lead"})
    )
    assert compute_confidence(blocked, 1) < compute_confidence(base, 1)


def test_confidence_is_bounded():
    f = Finding.model_validate(finding())
    assert 0 <= compute_confidence(f, 99) <= 99


def test_severity_is_capped_by_confidence():
    assert severity_cap("critical", 55) == "medium"
    assert severity_cap("critical", 35) == "low"
    assert severity_cap("critical", 90) == "critical"
    assert severity_cap("low", 90) == "low"


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------
def test_extract_records_survives_prose_and_fences(stub_hunt_response):
    assert len(extract_records(stub_hunt_response)) == 2


def test_markers_are_counted(stub_hunt_response):
    markers = count_markers(stub_hunt_response)
    assert markers == {"feynman": 1, "socratic": 1, "inversion": 1}


def test_agent_retries_once_then_gives_up():
    provider = StubProvider(default="no json here, sorry")
    agent = HuntAgent(AgentSpec(agent_id="access-control", role="lane", spawn_reason="test"), provider)
    result = asyncio.run(agent.run("contract C {}", "{}"))
    assert result.attempts == 2, "exactly one retry, never more"
    assert result.status == "invalid-output"


def test_agent_succeeds_on_first_attempt(stub_hunt_response):
    provider = StubProvider(default=stub_hunt_response)
    agent = HuntAgent(AgentSpec(agent_id="access-control", role="lane", spawn_reason="test"), provider)
    result = asyncio.run(agent.run("contract C {}", "{}"))
    assert result.status == "ok" and result.attempts == 1 and len(result.records) == 2


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------
def test_verifier_parses_a_refutation():
    provider = StubProvider(default=json.dumps({
        "verifier_verdict": "REFUTED",
        "refutation": "withdraw is nonReentrant; the mutex blocks the second entry.",
        "counter_evidence": "Vault.sol:12 `modifier nonReentrant`",
        "residual_risk": "The same pattern in emergencyWithdraw has no mutex.",
    }))
    v = asyncio.run(AdversarialVerifier(provider).verify_one(
        Finding.model_validate(finding()), "contract Vault {}"))
    assert v.verifier_verdict is VerifierVerdict.REFUTED
    assert v.residual_risk


def test_unparseable_verifier_output_is_not_run_not_confirmed():
    v = asyncio.run(AdversarialVerifier(StubProvider(default="I think it's probably fine")).verify_one(
        Finding.model_validate(finding()), "contract Vault {}"))
    assert v.verifier_verdict is VerifierVerdict.NOT_RUN, "silence must never read as confirmation"
    assert v.refutation


def test_below_threshold_findings_are_marked_not_run():
    findings = [Finding.model_validate(finding(severity="informational", kind="LEAD"))]
    stats = asyncio.run(AdversarialVerifier(StubProvider(default="")).verify_all(
        findings, {}, budget="standard"))
    assert stats["candidates_sent"] == 0
    assert findings[0].verification.verifier_verdict is VerifierVerdict.NOT_RUN


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def _map_for(source_root: pathlib.Path) -> tuple[SystemMap, dict[str, str]]:
    return build_system_map(source_root)


def test_full_run_produces_artifacts(tmp_path, vulnerable_source, stub_hunt_response):
    smap, sources = _map_for(vulnerable_source)
    smap.hot_functions = [
        HotFunction(contract="Vault", function="withdraw", risk_weight=0.9),
        HotFunction(contract="Vault", function="deposit", risk_weight=0.5),
    ]
    provider = StubProvider(default=stub_hunt_response)
    orch = Orchestrator(provider, tmp_path / "run", budget="quick")
    bundle = build_bundle(sources)

    result = asyncio.run(orch.run(smap, {"full": bundle}, {"Vault": bundle}))

    assert not result.get("aborted")
    assert result["reduced"], "a successful run must produce findings"
    for name in ("run.json", "roster.json", "reduced.json", "verified.json"):
        assert (tmp_path / "run" / name).exists(), f"{name} missing"

    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    # The oracle in the fixture pulls in specialists beyond the core six; the
    # manifest must account for exactly the roster that was actually built.
    assert manifest["quorum"]["expected_agents"] == len(build_roster(smap, "quick").agents)
    assert manifest["quorum"]["expected_agents"] >= 6
    assert all(a["spawn_reason"] for a in manifest["agents"])
    assert result["coverage"]["hot_function_axis_pairs"] > 0


def test_dead_fanout_aborts_instead_of_reporting(tmp_path, vulnerable_source):
    """A half-dead run's absences read as clean bills of health. Refuse to render."""
    smap, sources = _map_for(vulnerable_source)
    orch = Orchestrator(StubProvider(default="total garbage, no json"), tmp_path / "run", budget="quick")
    result = asyncio.run(orch.run(smap, {"full": build_bundle(sources)}, {}))
    assert result.get("aborted")
    assert "clean bill of health" in result["reason"]


def test_manifest_survives_a_failed_run(tmp_path, vulnerable_source):
    smap, sources = _map_for(vulnerable_source)
    orch = Orchestrator(StubProvider(default="garbage"), tmp_path / "run", budget="quick")
    asyncio.run(orch.run(smap, {"full": build_bundle(sources)}, {}))
    manifest = json.loads((tmp_path / "run" / "run.json").read_text())
    assert manifest["phases"], "a failed run must still leave a diagnosable manifest"
    assert any(p["status"] == "failed" for p in manifest["phases"])
