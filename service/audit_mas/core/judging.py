"""The four judging gates — executable version of orchestration/judging.md.

Why this exists
---------------
v3.0.0 documented four gates in fixed order and then shipped a service whose
JUDGE phase applied exactly two operations: REFUTED becomes rejected, and
severity gets clamped by confidence. `judgment.gate1_reachability` and its
siblings were never populated. The document described a process the code did
not run.

What is deterministic and what is not
-------------------------------------
Gates 2 and 4 are largely mechanical: impact follows from the axes and the
Devil's Advocate `by_design` dimension; fixability follows from the fix label.
Gates 1 and 3 need judgment in the general case — whether a path is truly
reachable is the question the whole audit is trying to answer.

So this module derives every verdict it honestly can from evidence already in
the record, and returns `UNCERTAIN` where it cannot. That is not a cop-out:
`judging.md` defines `UNCERTAIN = ALLOWS`, so an underivable gate fails open
toward reporting the finding, which is the correct bias for a security tool.

`judge()` takes an optional `overrides` argument so an LLM judge can supply
verdicts for the gates that need reasoning. When it does, its verdicts win and
the rationale records that they were model-supplied rather than derived.
"""

from __future__ import annotations

import re

from ..schemas import Judgment, Severity, SystemMap, VerifierVerdict

ALLOWS, BLOCKS, IRRELEVANT, UNCERTAIN = "ALLOWS", "BLOCKS", "IRRELEVANT", "UNCERTAIN"

# Gate 2: impact axes that represent real harm to someone other than the attacker.
HARM_AXES = {"theft", "accounting", "liveness", "provenance"}

# Actors whose action, if it is the harm step, triggers the admin-amplifier rule.
ADMIN_ACTORS = re.compile(
    r"\b(admin|owner|governance|multisig|timelock|operator|manager|dao|guardian)\b", re.IGNORECASE
)
# An attacker named anywhere in the path means the harm step is not admin-only.
ATTACKER_ACTORS = re.compile(
    r"\b(attacker|any\s?(one|user|caller)|unprivileged|permissionless|user|borrower|depositor|lp)\b",
    re.IGNORECASE,
)

AMPLIFIERS = {
    "race": re.compile(r"\b(race|front[- ]?run|mid[- ]?flow|window|in[- ]flight|before the update)\b", re.I),
    "retroactive-sweep": re.compile(r"\b(retroactive|already credited|pending value|rewrites?|sweep)\b", re.I),
    "asymmetric-formula": re.compile(r"\b(asymmetr|formula|chains? into|profits? from)\b", re.I),
    "access-gap": re.compile(r"\b(missing (guard|check|modifier)|unguarded|tautolog|uninitiali|init guard)\b", re.I),
}

SELF_HARM = re.compile(r"\b(self[- ]harm|only the caller|harms? only (them|the attacker)|own funds)\b", re.I)
GAS_ONLY = re.compile(r"\b(gas grief|gas only|wastes? gas)\b", re.I)

SEVERITY_RANK = ["informational", "low", "medium", "high", "critical"]


def _text(rec: dict) -> str:
    """Everything a gate might want to read, as one lowercase blob."""
    parts = [
        rec.get("path", ""),
        rec.get("description", ""),
        rec.get("root_cause", ""),
        (rec.get("proof") or {}).get("content", ""),
    ]
    for mech in rec.get("mechanisms") or []:
        parts.append(mech.get("root_cause") or "")
    return " ".join(str(p) for p in parts)


def _da(rec: dict, dim: str) -> tuple[bool, bool]:
    """Returns (blocks, has_bypass) for a Devil's Advocate dimension."""
    d = (rec.get("devils_advocate") or {}).get(dim) or {}
    return bool(d.get("blocks")), bool(d.get("bypass"))


def _verdict(v: str, note: str) -> dict:
    return {"verdict": v, "note": note[:300]}


# ---------------------------------------------------------------------------
# Gate 1 — Reachability
# ---------------------------------------------------------------------------
def gate1_reachability(rec: dict, smap: SystemMap | None) -> dict:
    verifier = (rec.get("verification") or {}).get("verifier_verdict")
    if verifier == VerifierVerdict.UNREACHABLE_CODE.value:
        return _verdict(BLOCKS, "Adversarial verifier found the defect real but unreachable.")

    blocks, bypass = _da(rec, "access")
    if blocks and not bypass:
        return _verdict(BLOCKS, "Agent's own DA records that access control holds, with no bypass named.")

    text = _text(rec)
    if ATTACKER_ACTORS.search(text):
        return _verdict(ALLOWS, "Path names an unprivileged actor as the caller.")

    if ADMIN_ACTORS.search(text):
        # Privileged entry point. Acquirable roles keep it open.
        if smap:
            acquirable = [t.actor for t in smap.trust_boundaries if t.acquirable]
            if acquirable:
                return _verdict(ALLOWS, f"Privileged path, but these roles are acquirable: {acquirable}.")
        return _verdict(UNCERTAIN, "Privileged path and no acquirable role recorded in the map; treat as ALLOWS.")

    return _verdict(UNCERTAIN, "No actor identified in the path; treat as ALLOWS.")


# ---------------------------------------------------------------------------
# Gate 2 — Impact
# ---------------------------------------------------------------------------
def gate2_impact(rec: dict, smap: SystemMap | None = None) -> dict:
    blocks, bypass = _da(rec, "by_design")
    if blocks and not bypass:
        return _verdict(IRRELEVANT, "Agent's own DA classifies this as intended design with no material harm.")

    text = _text(rec)
    if SELF_HARM.search(text):
        return _verdict(BLOCKS, "Harm lands only on the caller.")
    if GAS_ONLY.search(text) and not (set(rec.get("axes") or []) & {"theft", "accounting"}):
        return _verdict(BLOCKS, "Gas griefing with no lasting state damage.")

    econ_blocks, econ_bypass = _da(rec, "economic")
    if econ_blocks and not econ_bypass:
        return _verdict(BLOCKS, "Attack is not economically feasible per the agent's own DA.")

    axes = set(rec.get("axes") or [])
    if axes & HARM_AXES:
        return _verdict(ALLOWS, f"Covers harm axes {sorted(axes & HARM_AXES)}.")

    return _verdict(UNCERTAIN, "No harm axis asserted; treat as ALLOWS.")


# ---------------------------------------------------------------------------
# Gate 3 — Code-level defect
# ---------------------------------------------------------------------------
def gate3_code_defect(rec: dict, smap: SystemMap | None = None) -> dict:
    if rec.get("kind") == "LEAD":
        return _verdict(UNCERTAIN, "Lead: root cause not established in source yet.")

    proof = rec.get("proof") or {}
    if not proof.get("content"):
        return _verdict(UNCERTAIN, "No proof attached; treat as ALLOWS.")

    if not rec.get("root_cause"):
        return _verdict(UNCERTAIN, "No code-level root cause stated.")

    # "The oracle is honest" / "the admin would never" are assumptions, not defects.
    if re.search(r"\b(would never|assumes? the (admin|owner|oracle) is|trusts? the (admin|owner))\b",
                 _text(rec), re.I):
        return _verdict(BLOCKS, "Rests on an off-chain behavioural assumption rather than a code defect.")

    return _verdict(ALLOWS, f"Root cause stated and backed by {proof.get('kind')} proof.")


# ---------------------------------------------------------------------------
# Gate 4 — Fixability & minimality
# ---------------------------------------------------------------------------
def gate4_fixability(rec: dict, smap: SystemMap | None = None) -> dict:
    fixes = rec.get("fixes") or ([rec["fix"]] if rec.get("fix") else [])
    if not fixes:
        return _verdict(UNCERTAIN, "No fix proposed.")

    labels = {f.get("label") for f in fixes}
    if labels == {"redesign"}:
        return _verdict(BLOCKS, "Only remedy offered is a protocol redesign.")
    local = labels - {"redesign"}
    return _verdict(ALLOWS, f"Local fix available ({', '.join(sorted(local))}).")


# ---------------------------------------------------------------------------
# Admin amplifier
# ---------------------------------------------------------------------------
def admin_amplifier(rec: dict) -> str:
    """Applies only when the HARM step is an admin action, not merely the setup."""
    text = _text(rec)
    if not ADMIN_ACTORS.search(text):
        return "not-applicable"

    # Amplifiers are checked BEFORE the attacker test, not after. A "race"
    # amplifier is by definition an unprivileged user exploiting an admin
    # window - testing for an attacker first would shadow every amplifier the
    # rule exists to recognise.
    for name, pattern in AMPLIFIERS.items():
        if pattern.search(text):
            return name

    if ATTACKER_ACTORS.search(text):
        return "not-applicable"  # unprivileged actor drives the harm step
    return "none-named"


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------
def assign_severity(rec: dict, gates: dict, confidence: int) -> str:
    if any(gates[g]["verdict"] == BLOCKS for g in
           ("gate1_reachability", "gate2_impact", "gate3_code_defect")):
        return "rejected"
    if gates["gate2_impact"]["verdict"] == IRRELEVANT:
        return "rejected"

    axes = set(rec.get("axes") or [])
    permissionless = gates["gate1_reachability"]["verdict"] == ALLOWS and \
        ATTACKER_ACTORS.search(_text(rec)) is not None
    fund_loss = bool(axes & {"theft", "accounting"})
    availability = "liveness" in axes

    if fund_loss and permissionless:
        severity = "critical"
    elif fund_loss or availability:
        severity = "high"
    elif axes & {"provenance", "identity"}:
        severity = "medium"
    else:
        severity = "low"

    # Never present a claim as more certain than the arithmetic supports.
    claimed = rec.get("severity_claim", "medium")
    severity = SEVERITY_RANK[min(SEVERITY_RANK.index(severity), SEVERITY_RANK.index(claimed))]

    if confidence < 40:
        severity = SEVERITY_RANK[min(SEVERITY_RANK.index(severity), SEVERITY_RANK.index("low"))]
    elif confidence < 60:
        severity = SEVERITY_RANK[min(SEVERITY_RANK.index(severity), SEVERITY_RANK.index("medium"))]

    return severity


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
GATES = (
    ("gate1_reachability", gate1_reachability),
    ("gate2_impact", gate2_impact),
    ("gate3_code_defect", gate3_code_defect),
    ("gate4_fixability", gate4_fixability),
)


def judge(rec: dict, smap: SystemMap | None = None, overrides: dict | None = None) -> Judgment:
    """Run all four gates in fixed order and assign a final severity.

    `overrides` lets an LLM judge supply verdicts for gates that need reasoning;
    a supplied verdict wins and is marked as model-supplied in the rationale.
    """
    confidence = int((rec.get("judgment") or {}).get("confidence", 0))
    verifier = (rec.get("verification") or {}).get("verifier_verdict")

    # A refutation is terminal. Running gates on a dead claim wastes everyone's time.
    if verifier == VerifierVerdict.REFUTED.value:
        refutation = (rec.get("verification") or {}).get("refutation", "")
        return Judgment(
            admin_amplifier="not-applicable",
            final_severity="rejected",
            confidence=confidence,
            rationale=f"Refuted by the adversarial verifier: {refutation[:200]}",
        )

    overrides = overrides or {}
    gates: dict[str, dict] = {}
    model_supplied: list[str] = []
    for name, fn in GATES:
        if name in overrides:
            gates[name] = overrides[name]
            model_supplied.append(name)
        else:
            gates[name] = fn(rec, smap)

    amplifier = admin_amplifier(rec)
    if amplifier == "none-named":
        return Judgment(
            **gates,
            admin_amplifier=amplifier,
            final_severity="rejected",
            confidence=confidence,
            rationale="Harm step is an admin action with no unprivileged amplifier named.",
        )

    severity = assign_severity(rec, gates, confidence)
    reasons = [f"{n.split('_')[0]}={gates[n]['verdict']}" for n, _ in GATES]
    rationale = " ".join(reasons)
    if severity != rec.get("severity_claim"):
        rationale += f" | claim {rec.get('severity_claim')} -> {severity}"
    if model_supplied:
        rationale += f" | model-supplied: {', '.join(model_supplied)}"

    return Judgment(
        **gates,
        admin_amplifier=amplifier,
        final_severity=severity,
        confidence=confidence,
        rationale=rationale[:400],
    )


__all__ = ["judge", "admin_amplifier", "assign_severity", "Severity",
           "gate1_reachability", "gate2_impact", "gate3_code_defect", "gate4_fixability"]
