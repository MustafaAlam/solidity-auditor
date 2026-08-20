"""Reduction and confidence — the service-side twin of ``scripts/reduce.py``.

Both halves of this project must produce the same numbers on the same input,
otherwise a scorecard from the skill cannot be compared with one from the
service. ``tests/test_reduce_parity.py`` runs the same fixture through both and
asserts group counts and confidences match.

The constants live here and in ``scripts/reduce.py``. When you change one,
change both and run the parity test — it exists precisely to catch the drift.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re

from ..schemas import Finding, Kind, Severity

SEVERITY_ORDER = ["informational", "low", "medium", "high", "critical"]

SYNONYM_CLUSTERS: list[set[str]] = [
    {"reentrancy", "cross-function-reentrancy", "read-only-reentrancy", "reentrancy-guard-gap"},
    {"rounding-direction", "rounding-error", "precision-loss", "truncation"},
    {"missing-access-control", "missing-guard", "unprotected-function", "access-gap"},
    {"missing-nonce", "signature-replay", "replay-attack"},
    {"stale-oracle", "oracle-staleness", "missing-staleness-check"},
    {"storage-collision", "storage-layout-clash", "slot-collision"},
    {"unchecked-return", "unchecked-call", "ignored-return-value"},
    {"integer-overflow", "integer-underflow", "unsafe-cast", "unchecked-math"},
    {"dos", "denial-of-service", "permanent-dos", "griefing"},
]

PROOF_BASE = {"counterexample": 60, "numeric-trace": 60, "state-sequence": 55, "quoted-code": 45}
POC_BONUS = {"passing": 25, "compiled": 15, "sketch": 5, "not-feasible": 0}
VERIFIER_DELTA = {"CONFIRMED": 15, "WEAKENED": -15, "NOT-RUN": -5, "UNREACHABLE-CODE": -40, "REFUTED": -100}

_WS = re.compile(r"\s+")


def synonym_key(bug_class: str) -> str:
    for cluster in SYNONYM_CLUSTERS:
        if bug_class in cluster:
            return "syn:" + min(cluster)
    return bug_class


def normalize_add_lines(fix) -> tuple[str, ...]:
    lines = list(getattr(fix, "add_lines", None) or [])
    if not lines and getattr(fix, "diff", None):
        lines = [ln[1:] for ln in fix.diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    if not lines:
        lines = [getattr(fix, "summary", "") or ""]
    out = []
    for ln in lines:
        ln = _WS.sub(" ", ln.split("//")[0]).strip().rstrip(";")
        if ln:
            out.append(ln)
    return tuple(out)


def fix_fingerprint(fix) -> str:
    label = getattr(getattr(fix, "label", None), "value", getattr(fix, "label", None))
    payload = json.dumps({"label": label, "adds": normalize_add_lines(fix)}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def compute_confidence(f: Finding, corroborators: int) -> int:
    """Identical arithmetic to scripts/reduce.py::compute_confidence."""
    if f.kind is Kind.FINDING and f.proof is not None:
        score = PROOF_BASE.get(f.proof.kind, 40)
    else:
        score = 30

    score += min(15, 5 * max(0, corroborators - 1))
    if f.poc is not None:
        score += POC_BONUS.get(f.poc.status.value, 0)
    verdict = f.verification.verifier_verdict.value if f.verification else "NOT-RUN"
    score += VERIFIER_DELTA.get(verdict, -5)

    da = f.devils_advocate
    for name in ("guards", "reentrancy", "access", "by_design", "economic", "dry_run"):
        dim = getattr(da, name)
        if dim.blocks and not dim.bypass:
            score -= 10

    return max(0, min(99, score))


def reduce_findings(findings: list[Finding]) -> dict:
    """Group, preserve, and account. Function isolation is structural: `function`
    is part of the key, so cross-function merging is unrepresentable rather than
    merely discouraged."""
    hunts = [f for f in findings if f.kind in {Kind.FINDING, Kind.LEAD}]

    groups: dict[tuple[str, str, str], list[Finding]] = collections.defaultdict(list)
    for f in hunts:
        groups[(f.contract, f.function, synonym_key(f.bug_class))].append(f)

    reduced: list[dict] = []
    merge_queue: list[dict] = []

    for (contract, function, synkey), members in sorted(groups.items()):
        members.sort(
            key=lambda f: (
                f.kind is Kind.FINDING,
                SEVERITY_ORDER.index(f.severity_claim.value),
                len(f.proof.content) if f.proof else 0,
            ),
            reverse=True,
        )
        best = members[0]
        agents = sorted({m.agent_id for m in members})

        mechanisms, seen_mech = [], set()
        for m in members:
            text = _WS.sub(" ", (m.root_cause or m.description)).strip().lower()
            key = hashlib.sha256(text.encode()).hexdigest()[:12]
            if key in seen_mech:
                continue
            seen_mech.add(key)
            mechanisms.append(
                {"agent_id": m.agent_id, "bug_class": m.bug_class, "root_cause": m.root_cause or m.description}
            )

        fixes, seen_fix = [], set()
        for m in members:
            for fx in [m.fix, *m.alternatives]:
                fp = fix_fingerprint(fx)
                if fp in seen_fix:
                    continue
                seen_fix.add(fp)
                fixes.append({**fx.model_dump(mode="json", exclude_none=True),
                              "_fingerprint": fp, "_from_agent": m.agent_id})

        rec = best.model_dump(mode="json", exclude_none=True)
        rec.pop("fix", None)
        rec.pop("alternatives", None)
        rec["corroboration"] = agents
        rec["agent_count"] = len(agents)
        rec["mechanisms"] = mechanisms
        rec["fixes"] = fixes
        rec["axes"] = sorted({a.value for m in members for a in m.axes})
        rec.setdefault("judgment", {})
        rec["judgment"] = {**(rec.get("judgment") or {}), "confidence": compute_confidence(best, len(agents))}
        reduced.append(rec)

        distinct = {m.bug_class for m in members}
        if len(distinct) > 1 and not synkey.startswith("syn:"):
            merge_queue.append({
                "contract": contract, "function": function, "bug_classes": sorted(distinct),
                "question": "Same defect, or coexisting defects that must stay separate?",
            })

    by_func: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in reduced:
        by_func[(r["contract"], r["function"])].append(r)
    coexisting = [
        {"contract": c, "function": f, "group_keys": [r["group_key"] for r in rs]}
        for (c, f), rs in sorted(by_func.items()) if len(rs) > 1
    ]

    raw_pairs = {(f.contract, f.function) for f in hunts}
    final_pairs = {(r["contract"], r["function"]) for r in reduced}
    dropped = sorted(raw_pairs - final_pairs)

    reduced.sort(key=lambda r: (
        -SEVERITY_ORDER.index(r["severity_claim"]),
        -int(r["judgment"]["confidence"]),
        r["contract"], r["function"],
    ))

    return {
        "reduced": reduced,
        "merge_queue": merge_queue,
        "coexisting": coexisting,
        "completeness": {
            "unique_contract_function_raw": len(raw_pairs),
            "unique_contract_function_final": len(final_pairs),
            "dropped": [f"{c}.{f}" for c, f in dropped],
            "ok": not dropped,
        },
    }


def axis_coverage(hot_functions, findings: list[Finding]) -> tuple[list[dict], dict]:
    covered = {(f.contract, f.function, a.value) for f in findings for a in f.axes}
    gaps, total, hits = [], 0, 0
    for hf in hot_functions:
        for ax in hf.required_axes:
            total += 1
            if (hf.contract, hf.function, ax.value) in covered:
                hits += 1
            else:
                gaps.append({"contract": hf.contract, "function": hf.function, "axis": ax.value,
                             "risk_weight": hf.risk_weight})
    return gaps, {"hot_function_axis_pairs": total, "covered": hits, "axisgap_leads": len(gaps)}


def severity_cap(severity: str, confidence: int) -> str:
    """A critical the system privately doubts is a medium that needs more work."""
    if confidence < 40:
        return "low" if SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index("low") else severity
    if confidence < 60:
        return "medium" if SEVERITY_ORDER.index(severity) > SEVERITY_ORDER.index("medium") else severity
    return severity


__all__ = [
    "Severity", "reduce_findings", "compute_confidence", "axis_coverage",
    "severity_cap", "synonym_key", "fix_fingerprint",
]
