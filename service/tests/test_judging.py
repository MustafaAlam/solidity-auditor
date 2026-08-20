"""The four gates — the phase the service documented but did not run.

v3.0.0's `reduce_and_judge` applied REFUTED->rejected and a confidence clamp,
and nothing else. `judgment.gate1_reachability` and its siblings stayed null
while `judging.md` described four gates in fixed order. These tests pin the
behaviour so the doc and the code cannot drift apart again.
"""

from __future__ import annotations

import pytest
from conftest import da, finding

from audit_mas.core.judging import GATES, admin_amplifier, judge
from audit_mas.schemas import Evidence, Scope, SystemMap, TrustBoundary


def reduced(**kw) -> dict:
    """A finding shaped the way reduction hands it to the judge."""
    rec = finding(**kw)
    fix = rec.pop("fix")
    rec["fixes"] = [fix]
    rec.setdefault("judgment", {})["confidence"] = kw.pop("_confidence", 80)
    return rec


# ---------------------------------------------------------------------------
def test_all_four_gates_are_populated():
    j = judge(reduced())
    for name, _ in GATES:
        assert getattr(j, name) is not None, f"{name} was never evaluated"
        assert getattr(j, name)["verdict"] in {"ALLOWS", "BLOCKS", "IRRELEVANT", "UNCERTAIN"}


def test_gates_run_in_fixed_order():
    assert [n for n, _ in GATES] == [
        "gate1_reachability", "gate2_impact", "gate3_code_defect", "gate4_fixability"
    ]


def test_refutation_is_terminal_and_skips_the_gates():
    rec = reduced()
    rec["verification"] = {"verifier_verdict": "REFUTED", "refutation": "nonReentrant blocks re-entry"}
    j = judge(rec)
    assert j.final_severity == "rejected"
    assert "nonReentrant" in j.rationale
    assert j.gate1_reachability is None, "no point gating a dead claim"


# ---------------------------------------------------------------------------
# Gate 1
# ---------------------------------------------------------------------------
def test_gate1_blocks_when_verifier_found_it_unreachable():
    rec = reduced()
    rec["verification"] = {"verifier_verdict": "UNREACHABLE-CODE", "refutation": "no caller"}
    assert judge(rec).gate1_reachability["verdict"] == "BLOCKS"


def test_gate1_blocks_when_the_agents_own_da_says_access_holds():
    rec = reduced()
    rec["devils_advocate"] = da(block="access")
    assert judge(rec).gate1_reachability["verdict"] == "BLOCKS"


def test_gate1_allows_when_a_bypass_is_named():
    rec = reduced()
    rec["devils_advocate"] = da(block="access", bypass="initialize() is unguarded")
    assert judge(rec).gate1_reachability["verdict"] == "ALLOWS"


def test_gate1_allows_privileged_path_when_the_role_is_acquirable():
    rec = reduced()
    rec["path"] = "owner -> Vault.sweep -> transfers reserve"
    rec["description"] = "The owner can sweep the reserve balance to an arbitrary address."
    rec["root_cause"] = "sweep has no timelock."
    smap = SystemMap(
        scope=Scope(total_sloc=100),
        evidence=Evidence(),
        trust_boundaries=[TrustBoundary(actor="owner", trust="semi-trusted",
                                        powers=["sweep"], acquirable=True)],
    )
    assert judge(rec, smap).gate1_reachability["verdict"] == "ALLOWS"


# ---------------------------------------------------------------------------
# Gate 2
# ---------------------------------------------------------------------------
def test_gate2_treats_by_design_as_irrelevant_and_rejects():
    rec = reduced()
    rec["devils_advocate"] = da(block="by_design")
    j = judge(rec)
    assert j.gate2_impact["verdict"] == "IRRELEVANT"
    assert j.final_severity == "rejected"


def test_gate2_blocks_self_harm():
    rec = reduced()
    rec["description"] = "The caller burns their own funds; harms only the attacker and nobody else."
    assert judge(rec).gate2_impact["verdict"] == "BLOCKS"


def test_gate2_blocks_when_economics_do_not_work():
    rec = reduced()
    rec["devils_advocate"] = da(block="economic")
    assert judge(rec).gate2_impact["verdict"] == "BLOCKS"


def test_gate2_allows_on_a_harm_axis():
    assert judge(reduced()).gate2_impact["verdict"] == "ALLOWS"


# ---------------------------------------------------------------------------
# Gate 3
# ---------------------------------------------------------------------------
def test_gate3_blocks_an_offchain_assumption():
    rec = reduced()
    rec["root_cause"] = "The contract assumes the admin is honest when setting the rate."
    assert judge(rec).gate3_code_defect["verdict"] == "BLOCKS"


def test_gate3_allows_a_proven_code_defect():
    assert judge(reduced()).gate3_code_defect["verdict"] == "ALLOWS"


# ---------------------------------------------------------------------------
# Gate 4
# ---------------------------------------------------------------------------
def test_gate4_blocks_when_only_a_redesign_is_offered():
    rec = reduced()
    rec["fixes"] = [{"label": "redesign", "summary": "Rewrite the accounting model."}]
    j = judge(rec)
    assert j.gate4_fixability["verdict"] == "BLOCKS"
    # Gate 4 alone does not reject - judging.md only excludes on gates 1-3.
    assert j.final_severity != "rejected"


def test_gate4_allows_when_a_local_fix_exists_alongside_a_redesign():
    rec = reduced()
    rec["fixes"] = [{"label": "redesign", "summary": "Rewrite it."},
                    {"label": "validate", "summary": "Add a bound check."}]
    assert judge(rec).gate4_fixability["verdict"] == "ALLOWS"


# ---------------------------------------------------------------------------
# admin amplifier
# ---------------------------------------------------------------------------
def test_admin_only_harm_without_an_amplifier_is_rejected():
    rec = reduced()
    rec["path"] = "owner -> Vault.setRate -> rate updated"
    rec["description"] = "The owner can set an arbitrary rate at any time."
    rec["root_cause"] = "setRate has no bound."
    j = judge(rec)
    assert j.admin_amplifier == "none-named"
    assert j.final_severity == "rejected"


@pytest.mark.parametrize("phrase,expected", [
    ("an unprivileged user can front-run the update window", "race"),
    ("the update is retroactive and rewrites value already credited", "retroactive-sweep"),
    ("the asymmetric formula chains into a value an actor profits from", "asymmetric-formula"),
    ("a missing guard on the initializer", "access-gap"),
])
def test_named_amplifiers_are_recognised(phrase, expected):
    rec = reduced()
    rec["path"] = "owner -> Vault.setRate -> rate updated"
    rec["description"] = f"The owner sets the rate; {phrase}."
    rec["root_cause"] = "setRate is unbounded."
    assert admin_amplifier(rec) == expected


def test_attacker_in_the_chain_makes_the_rule_inapplicable():
    rec = reduced()
    rec["path"] = "admin sets rate -> attacker calls Vault.swap -> drains reserve"
    assert admin_amplifier(rec) == "not-applicable"


# ---------------------------------------------------------------------------
# severity
# ---------------------------------------------------------------------------
def test_permissionless_fund_loss_is_critical_when_claimed_critical():
    rec = reduced(severity="critical")
    assert judge(rec).final_severity == "critical"


def test_severity_never_exceeds_the_agents_own_claim():
    """The gates can demote a claim; they must not promote one."""
    assert judge(reduced(severity="low")).final_severity == "low"


@pytest.mark.parametrize("confidence,expected_cap", [(90, "critical"), (55, "medium"), (35, "low")])
def test_confidence_caps_severity(confidence, expected_cap):
    rec = reduced(severity="critical")
    rec["judgment"]["confidence"] = confidence
    assert judge(rec).final_severity == expected_cap


def test_llm_override_wins_and_is_recorded():
    rec = reduced()
    override = {"gate1_reachability": {"verdict": "BLOCKS", "note": "guarded by onlyRelayer at L88"}}
    j = judge(rec, overrides=override)
    assert j.gate1_reachability["verdict"] == "BLOCKS"
    assert j.final_severity == "rejected"
    assert "model-supplied" in j.rationale
