#!/usr/bin/env python3
"""
reduce.py — deterministic reduction of validated findings.

Why this exists
---------------
v2.7 asked the orchestrating model to perform dedup, fix preservation,
completeness accounting and axis coverage by following ~60 lines of prose
rules. Those rules are mechanical, so an LLM executing them is strictly worse
than code: it is slower, costs tokens, and silently drops records under load.

v3 moves every mechanical step here and leaves the model exactly one job that
genuinely needs judgment: deciding whether two differently-named bug_class
tags describe the same defect. That question is emitted as an explicit
adjudication queue (`merge_queue.json`) rather than being assumed.

Guarantees this script enforces (these are the v2.7 "HARD GATES", now code):
  * Function isolation      - records are never merged across `function`.
  * Wide description        - every distinct mechanism in a group survives.
  * Fix preservation        - distinct fixes become Option A / B / ... verbatim.
  * Completeness            - every unique (contract, function) in input
                              appears in output, or the run fails loudly.
  * Axis coverage           - uncovered (hot function, axis) pairs become
                              AXISGAP leads, bounded by required_axes.

Usage
-----
  python3 reduce.py --ledger .audit-x/ledger --system-map .audit-x/system-map.json \
                    --out .audit-x/reduced.json
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

SEVERITY_ORDER = ["informational", "low", "medium", "high", "critical"]
ALL_AXES = ["theft", "liveness", "accounting", "provenance", "boundary", "identity"]

# bug_class tags that are synonyms in practice. Merging inside a
# (contract, function) is safe for these; anything else goes to the human /
# model adjudication queue instead of being merged silently.
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


def synonym_key(bug_class: str) -> str:
    for cluster in SYNONYM_CLUSTERS:
        if bug_class in cluster:
            return "syn:" + min(cluster)
    return bug_class


# --------------------------------------------------------------------------
# Fix distinctness
# --------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def normalize_add_lines(fix: dict) -> tuple[str, ...]:
    """Reduce a fix to the set of lines it ADDS, whitespace- and comment-normalized.

    Two fixes are the same fix iff they add the same code. This replaces the
    v2.7 prose rule ("distinct if ADD-lines differ in called function, check
    direction, or checked parameter") with something reproducible.
    """
    lines: list[str] = list(fix.get("add_lines") or [])
    if not lines and fix.get("diff"):
        lines = [ln[1:] for ln in str(fix["diff"]).splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    if not lines:
        # Fall back to the summary so a fix with no diff still participates in
        # distinctness rather than colliding with every other diff-less fix.
        lines = [str(fix.get("summary", ""))]
    out = []
    for ln in lines:
        ln = ln.split("//")[0]
        ln = _WS.sub(" ", ln).strip().rstrip(";")
        if ln:
            out.append(ln)
    return tuple(out)


def fix_fingerprint(fix: dict) -> str:
    payload = json.dumps(
        {"label": fix.get("label"), "adds": normalize_add_lines(fix)}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------
PROOF_BASE = {"counterexample": 60, "numeric-trace": 60, "state-sequence": 55, "quoted-code": 45}
POC_BONUS = {"passing": 25, "compiled": 15, "sketch": 5, "not-feasible": 0}
VERIFIER_DELTA = {"CONFIRMED": 15, "WEAKENED": -15, "NOT-RUN": -5, "UNREACHABLE-CODE": -40, "REFUTED": -100}


def compute_confidence(rec: dict, corroborators: int) -> int:
    """Deterministic confidence. Never hand-picked, always reproducible.

    The inputs are: strength of proof, independent corroboration, PoC status,
    adversarial-verifier verdict, and unbypassed Devil's Advocate blockers.
    """
    if rec.get("kind") == "FINDING":
        score = PROOF_BASE.get((rec.get("proof") or {}).get("kind", ""), 40)
    else:
        score = 30

    score += min(15, 5 * max(0, corroborators - 1))
    score += POC_BONUS.get((rec.get("poc") or {}).get("status", ""), 0)
    score += VERIFIER_DELTA.get((rec.get("verification") or {}).get("verifier_verdict", "NOT-RUN"), -5)

    da = rec.get("devils_advocate") or {}
    for dim in ("guards", "reentrancy", "access", "by_design", "economic", "dry_run"):
        d = da.get(dim) or {}
        if d.get("blocks") and not d.get("bypass"):
            score -= 10

    return max(0, min(99, score))


# --------------------------------------------------------------------------
# Reduction
# --------------------------------------------------------------------------
def load_valid(ledger: pathlib.Path) -> list[dict]:
    f = ledger / "valid.jsonl"
    if not f.exists():
        sys.exit(f"{f} not found - run validate_findings.py first")
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def reduce_records(records: list[dict]) -> dict[str, Any]:
    hunts = [r for r in records if r.get("kind") in {"FINDING", "LEAD"}]
    notes = [r for r in records if r.get("kind") == "COVERAGE_NOTE"]

    # ---- pass 1: group by (contract, function, synonym(bug_class)) ---------
    # Function isolation is structural here: `function` is part of the key, so
    # cross-function merging is not merely discouraged, it is unrepresentable.
    groups: dict[tuple[str, str, str], list[dict]] = collections.defaultdict(list)
    for r in hunts:
        groups[(r["contract"], r["function"], synonym_key(r["bug_class"]))].append(r)

    merge_queue: list[dict] = []
    reduced: list[dict] = []

    for (contract, function, synkey), members in sorted(groups.items()):
        # Best record = FINDING over LEAD, then strongest severity claim, then
        # the one carrying the most evidence.
        def rank(r: dict) -> tuple:
            return (
                r.get("kind") == "FINDING",
                SEVERITY_ORDER.index(r.get("severity_claim", "informational")),
                len(json.dumps(r.get("proof") or {})),
                len(json.dumps(r.get("poc") or {})),
            )

        members_sorted = sorted(members, key=rank, reverse=True)
        best = dict(members_sorted[0])
        agents = sorted({m["agent_id"] for m in members})

        # ---- wide description: keep every distinct mechanism --------------
        mechanisms: list[dict] = []
        seen_mech: set[str] = set()
        for m in members_sorted:
            key = hashlib.sha256(
                _WS.sub(" ", (m.get("root_cause") or m.get("description") or "")).strip().lower().encode()
            ).hexdigest()[:12]
            if key in seen_mech:
                continue
            seen_mech.add(key)
            mechanisms.append(
                {
                    "agent_id": m["agent_id"],
                    "bug_class": m["bug_class"],
                    "root_cause": m.get("root_cause") or m.get("description"),
                    "path": m.get("path"),
                    "proof": m.get("proof"),
                }
            )

        # ---- fix preservation --------------------------------------------
        fixes: list[dict] = []
        seen_fix: set[str] = set()
        for m in members_sorted:
            for fx in [m.get("fix")] + list(m.get("alternatives") or []):
                if not fx:
                    continue
                fp = fix_fingerprint(fx)
                if fp in seen_fix:
                    continue
                seen_fix.add(fp)
                fixes.append({**fx, "_fingerprint": fp, "_from_agent": m["agent_id"]})

        # ---- axes union ---------------------------------------------------
        axes: set[str] = set()
        for m in members_sorted:
            axes.update(m.get("axes") or [])

        best["corroboration"] = agents
        best["mechanisms"] = mechanisms
        best["fixes"] = fixes
        best["axes"] = sorted(axes)
        best["agent_count"] = len(agents)
        best.pop("fix", None)
        best.pop("alternatives", None)
        best["judgment"] = dict(best.get("judgment") or {})
        best["judgment"]["confidence"] = compute_confidence(best, len(agents))
        reduced.append(best)

        # ---- adjudication queue -------------------------------------------
        distinct_classes = {m["bug_class"] for m in members}
        if len(distinct_classes) > 1 and not synkey.startswith("syn:"):
            merge_queue.append(
                {
                    "reason": "same (contract, function) reported under different bug_class tags",
                    "contract": contract,
                    "function": function,
                    "bug_classes": sorted(distinct_classes),
                    "question": "Are these the same defect? If yes they stay merged; if no, split into separate findings before reporting.",
                }
            )

    # ---- pass 2: function-level review, WITHOUT merging -------------------
    # v2.7 ran a "function-level second pass" that risked collapsing coexisting
    # bugs. Here it only reports co-located findings so the writer can check
    # that no mechanism was lost; it never merges.
    by_func: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in reduced:
        by_func[(r["contract"], r["function"])].append(r)
    coexisting = [
        {
            "contract": c,
            "function": f,
            "group_keys": [r["group_key"] for r in rs],
            "note": "Multiple coexisting defects in one function. All must appear in the report.",
        }
        for (c, f), rs in sorted(by_func.items())
        if len(rs) > 1
    ]

    # ---- completeness accounting -----------------------------------------
    raw_pairs = {(r["contract"], r["function"]) for r in hunts}
    final_pairs = {(r["contract"], r["function"]) for r in reduced}
    dropped = sorted(raw_pairs - final_pairs)

    return {
        "reduced": sorted(
            reduced,
            key=lambda r: (
                -SEVERITY_ORDER.index(r.get("severity_claim", "informational")),
                -int(r["judgment"]["confidence"]),
                r["contract"],
                r["function"],
            ),
        ),
        "merge_queue": merge_queue,
        "coexisting": coexisting,
        "coverage_notes": notes,
        "completeness": {
            "unique_contract_function_raw": len(raw_pairs),
            "unique_contract_function_final": len(final_pairs),
            "dropped": [f"{c}.{f}" for c, f in dropped],
            "ok": not dropped,
        },
    }


# --------------------------------------------------------------------------
# Axis coverage gate
# --------------------------------------------------------------------------
def axis_gaps(system_map: dict, records: list[dict]) -> tuple[list[dict], dict]:
    """Emit AXISGAP leads for hot (function, axis) pairs nobody examined.

    `required_axes` in the system map bounds this. Without it, a 40-function
    map generates 240 pairs and the report drowns in noise - which is why v3
    makes the map declare which axes actually matter per function.
    """
    covered: set[tuple[str, str, str]] = set()
    for r in records:
        for ax in r.get("axes") or []:
            covered.add((r["contract"], r["function"], ax))

    gaps: list[dict] = []
    total = 0
    hits = 0
    for hf in system_map.get("hot_functions", []):
        weight = float(hf.get("risk_weight", 0))
        required = hf.get("required_axes") or (ALL_AXES if weight >= 0.6 else ["theft", "accounting", "liveness"])
        for ax in required:
            total += 1
            if (hf["contract"], hf["function"], ax) in covered:
                hits += 1
                continue
            gaps.append(
                {
                    "schema_version": "3.0.0",
                    "kind": "AXISGAP",
                    "agent_id": "orchestrator",
                    "contract": hf["contract"],
                    "function": hf["function"],
                    "bug_class": f"axisgap-{ax}",
                    "group_key": f"{hf['contract']}|{hf['function']}|axisgap-{ax}",
                    "lane": "gap-seam",
                    "axes": [ax],
                    "severity_claim": "informational",
                    "description": (
                        f"No agent examined {hf['contract']}.{hf['function']} under the '{ax}' risk axis "
                        f"(risk_weight {weight}). This is a coverage gap, not a defect."
                    ),
                    "path": "n/a - coverage gap",
                    "fix": {"label": "validate", "summary": f"Re-run a targeted pass on {hf['function']} for the {ax} axis."},
                    "devils_advocate": {
                        d: {"note": "n/a for coverage gaps", "blocks": False}
                        for d in ("guards", "reentrancy", "access", "by_design", "economic", "dry_run")
                    }
                    | {"verdict": "survives"},
                }
            )
    return gaps, {"hot_function_axis_pairs": total, "covered": hits, "axisgap_leads": len(gaps)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministically reduce validated finding records.")
    ap.add_argument("--ledger", type=pathlib.Path, required=True)
    ap.add_argument("--system-map", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--fail-on-drop", action="store_true", help="Exit 1 if any (contract, function) was lost.")
    args = ap.parse_args()

    records = load_valid(args.ledger)
    result = reduce_records(records)

    if args.system_map and args.system_map.exists():
        smap = json.loads(args.system_map.read_text(encoding="utf-8"))
        gaps, cov = axis_gaps(smap, records)
        result["axis_gaps"] = gaps
        result["coverage"] = cov
    else:
        result["axis_gaps"] = []
        result["coverage"] = {"hot_function_axis_pairs": 0, "covered": 0, "axisgap_leads": 0, "note": "no system map supplied"}

    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    c = result["completeness"]
    cov = result["coverage"]
    print(f"Reduced {len(records)} records -> {len(result['reduced'])} groups")
    print(f"Completeness: {c['unique_contract_function_raw']} unique (Contract, function) in raw, "
          f"{c['unique_contract_function_final']} covered in final.")
    if c["dropped"]:
        print(f"  !! DROPPED: {', '.join(c['dropped'])}")
    print(f"AxisCoverage: {cov['covered']}/{cov['hot_function_axis_pairs']} hot-function-axis pairs covered; "
          f"{cov['axisgap_leads']} AXISGAP LEADs generated.")
    if result["merge_queue"]:
        print(f"Adjudication needed on {len(result['merge_queue'])} group(s) - see merge_queue in {args.out}")
    if result["coexisting"]:
        print(f"{len(result['coexisting'])} function(s) hold multiple coexisting defects - all must be reported.")

    return 1 if (args.fail_on_drop and c["dropped"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
