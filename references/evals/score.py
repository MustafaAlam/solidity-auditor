#!/usr/bin/env python3
"""
score.py — measure an audit run against ground truth.

Why this exists
---------------
v2.7's changelog claimed four consecutive improvements without a single number.
"Added a Devil's Advocate protocol" is a description of a change, not evidence
that it helped. This script is what turns a version bump from an assertion into
a measurement.

What it measures
----------------
  recall     of the known bugs, how many did the run find?
  precision  of what it reported, how much was real?
  FP rate    how much noise per case?
  severity   did it rank the real ones correctly?
  calibration does confidence 70-84 actually mean ~70-84% true positives?
  cost       tokens and context amplification per case

Matching
--------
A predicted finding matches a ground-truth bug when contract and function agree
and the bug classes are equal or synonymous. Function agreement is required
because a "reentrancy" reported in the wrong function is not the same bug -
that leniency is how audit tools flatter themselves.

Usage
-----
  python3 score.py --runs results/ --ground-truth ground-truth/ --out scorecard.md
  python3 score.py --runs results/ --ground-truth ground-truth/ --calibration
  python3 score.py --runs results/ --ground-truth ground-truth/ --baseline v2.7.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

SEVERITIES = ["informational", "low", "medium", "high", "critical"]

SYNONYMS: list[set[str]] = [
    {"reentrancy", "cross-function-reentrancy", "read-only-reentrancy", "reentrancy-guard-gap"},
    {"rounding-direction", "rounding-error", "precision-loss", "truncation", "share-inflation"},
    {"missing-access-control", "missing-guard", "unprotected-function", "access-gap", "missing-modifier"},
    {"missing-nonce", "signature-replay", "replay-attack", "cross-chain-replay"},
    {"stale-oracle", "oracle-staleness", "missing-staleness-check", "price-manipulation"},
    {"storage-collision", "storage-layout-clash", "slot-collision", "uninitialized-proxy"},
    {"integer-overflow", "integer-underflow", "unsafe-cast", "unchecked-math"},
    {"dos", "denial-of-service", "permanent-dos", "griefing", "unbounded-loop"},
    {"front-running", "mev", "sandwich", "missing-slippage", "missing-deadline"},
]


def canon(bug_class: str) -> str:
    bc = (bug_class or "").strip().lower()
    for group in SYNONYMS:
        if bc in group:
            return min(group)
    return bc


def key_of(rec: dict) -> tuple[str, str, str]:
    return (rec.get("contract", "").lower(), rec.get("function", "").lower(), canon(rec.get("bug_class", "")))


def loose_key(rec: dict) -> tuple[str, str]:
    return (rec.get("contract", "").lower(), rec.get("function", "").lower())


def final_severity(rec: dict) -> str:
    j = rec.get("judgment") or {}
    return j.get("final_severity") or rec.get("severity_claim") or "informational"


def confidence(rec: dict) -> int:
    return int((rec.get("judgment") or {}).get("confidence", 0))


# --------------------------------------------------------------------------
def score_case(predicted: list[dict], truth: list[dict]) -> dict:
    """Score one contract case.

    Reported = findings that survived gating. Leads are scored separately and
    much more gently: a lead is an explicit "I am not sure", so counting it as a
    false positive would punish exactly the honesty the system is trying to
    encourage.
    """
    reported = [p for p in predicted if p.get("kind") == "FINDING" and final_severity(p) != "rejected"]
    leads = [p for p in predicted if p.get("kind") == "LEAD" and final_severity(p) != "rejected"]

    truth_keys = {key_of(t): t for t in truth}
    truth_loose = {loose_key(t) for t in truth}

    matched: dict[tuple, dict] = {}
    false_positives: list[dict] = []
    near_misses: list[dict] = []  # right function, wrong bug class

    for p in reported:
        k = key_of(p)
        if k in truth_keys and k not in matched:
            matched[k] = p
        elif k in truth_keys:
            pass  # duplicate of an already-matched bug; not a new TP, not an FP
        elif loose_key(p) in truth_loose:
            near_misses.append(p)
        else:
            false_positives.append(p)

    # Leads that point at a real bug are partial credit, tracked but not counted
    # in precision/recall.
    lead_hits = [ld for ld in leads if key_of(ld) in truth_keys and key_of(ld) not in matched]

    tp = len(matched)
    fp = len(false_positives)
    fn = len(truth_keys) - tp

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / len(truth_keys) if truth_keys else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # Severity agreement on the ones actually found.
    sev_exact = 0
    sev_within_one = 0
    for k, p in matched.items():
        want = truth_keys[k].get("severity", "medium")
        got = final_severity(p)
        if want == got:
            sev_exact += 1
        if abs(SEVERITIES.index(want) - SEVERITIES.index(got)) <= 1:
            sev_within_one += 1

    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "near_misses": len(near_misses),
        "lead_hits": len(lead_hits),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "severity_exact": sev_exact,
        "severity_within_one": sev_within_one,
        "reported_total": len(reported),
        "leads_total": len(leads),
        # Display the original bug_class, not the canonicalized cluster name -
        # "share-inflation" is what the report called it and what a reader will
        # go looking for.
        "missed": [
            f"{truth_keys[k]['contract']}.{truth_keys[k]['function']} ({truth_keys[k].get('bug_class')})"
            for k in sorted(set(truth_keys) - set(matched))
        ],
        "spurious": [f"{p['contract']}.{p['function']} ({p.get('bug_class')})" for p in false_positives],
        "_matched_records": list(matched.values()),
        "_fp_records": false_positives,
    }


def calibration(all_matched: list[dict], all_fp: list[dict]) -> list[dict]:
    """Bin every reported finding by confidence and report the observed TP rate.

    A well-calibrated system has the 70-84 bin land near 70-84% true positives.
    Systematic drift means the constants in reduce.py need retuning.
    """
    bins = [(0, 39), (40, 59), (60, 69), (70, 84), (85, 99)]
    tagged = [(confidence(r), True) for r in all_matched] + [(confidence(r), False) for r in all_fp]
    out = []
    for lo, hi in bins:
        members = [t for c, t in tagged if lo <= c <= hi]
        n = len(members)
        out.append(
            {
                "bin": f"{lo}-{hi}",
                "n": n,
                "observed_tp_rate": round(sum(members) / n, 3) if n else None,
                "expected_midpoint": round((lo + hi) / 200, 3),
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Score audit runs against ground truth.")
    ap.add_argument("--runs", type=pathlib.Path, required=True,
                    help="Directory of <case_id>.json files, each a reduced.json from a run.")
    ap.add_argument("--ground-truth", type=pathlib.Path, required=True,
                    help="Directory of <case_id>.json ground-truth files.")
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("scorecard.md"))
    ap.add_argument("--calibration", action="store_true")
    ap.add_argument("--baseline", type=pathlib.Path, help="A previous scorecard.json to diff against.")
    ap.add_argument("--json-out", type=pathlib.Path, help="Also write machine-readable results here.")
    args = ap.parse_args()

    gt_files = sorted(args.ground_truth.glob("*.json"))
    if not gt_files:
        sys.exit(f"no ground-truth files in {args.ground_truth}")

    per_case: dict[str, dict] = {}
    all_matched: list[dict] = []
    all_fp: list[dict] = []
    missing_runs: list[str] = []

    for gt_file in gt_files:
        case_id = gt_file.stem
        run_file = args.runs / f"{case_id}.json"
        if not run_file.exists():
            missing_runs.append(case_id)
            continue
        truth = json.loads(gt_file.read_text(encoding="utf-8")).get("bugs", [])
        run = json.loads(run_file.read_text(encoding="utf-8"))
        predicted = run.get("reduced", run if isinstance(run, list) else [])
        res = score_case(predicted, truth)
        all_matched.extend(res.pop("_matched_records"))
        all_fp.extend(res.pop("_fp_records"))
        per_case[case_id] = res

    if not per_case:
        sys.exit("no runs matched any ground-truth case")

    agg = {
        k: sum(v[k] for v in per_case.values())
        for k in ("true_positives", "false_positives", "false_negatives", "near_misses",
                  "lead_hits", "severity_exact", "severity_within_one", "reported_total", "leads_total")
    }
    tp, fp, fn = agg["true_positives"], agg["false_positives"], agg["false_negatives"]
    agg["precision"] = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    agg["recall"] = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    agg["f1"] = round(2 * agg["precision"] * agg["recall"] / (agg["precision"] + agg["recall"]), 4) \
        if (agg["precision"] + agg["recall"]) else 0.0
    agg["fp_per_case"] = round(fp / len(per_case), 2)
    agg["cases"] = len(per_case)

    cal = calibration(all_matched, all_fp) if args.calibration else []

    # ---- render -----------------------------------------------------------
    L = ["# Audit eval scorecard", ""]
    L += [f"Cases scored: **{agg['cases']}**" + (f" (missing runs: {', '.join(missing_runs)})" if missing_runs else ""), ""]
    L += ["| Metric | Value |", "| --- | --- |"]
    L += [
        f"| Recall | **{agg['recall']:.1%}** ({tp}/{tp + fn} known bugs found) |",
        f"| Precision | **{agg['precision']:.1%}** ({tp}/{tp + fp} reported were real) |",
        f"| F1 | **{agg['f1']:.3f}** |",
        f"| False positives per case | {agg['fp_per_case']} |",
        f"| Near misses (right function, wrong class) | {agg['near_misses']} |",
        f"| Leads pointing at real bugs | {agg['lead_hits']} |",
        f"| Severity exact | {agg['severity_exact']}/{tp} |",
        f"| Severity within one level | {agg['severity_within_one']}/{tp} |",
        "",
    ]

    if args.baseline and args.baseline.exists():
        base = json.loads(args.baseline.read_text(encoding="utf-8")).get("aggregate", {})
        L += ["## Change vs baseline", "", "| Metric | Baseline | Now | Δ |", "| --- | --- | --- | --- |"]
        for m in ("recall", "precision", "f1", "fp_per_case"):
            b, n = base.get(m), agg.get(m)
            if b is None:
                continue
            arrow = "→" if abs(n - b) < 1e-9 else ("↑" if n > b else "↓")
            L.append(f"| {m} | {b} | {n} | {arrow} {n - b:+.4f} |")
        L.append("")

    if cal:
        L += ["## Calibration", "",
              "Observed true-positive rate per confidence bin. A calibrated system tracks the expected column.",
              "", "| Bin | n | Observed TP rate | Expected |", "| --- | --- | --- | --- |"]
        for c in cal:
            rate = c["observed_tp_rate"]
            rate_str = "—" if rate is None else f"{rate:.1%}"
            L.append(f"| {c['bin']} | {c['n']} | {rate_str} | {c['expected_midpoint']:.0%} |")
        L.append("")

    L += ["## Per case", "", "| Case | Recall | Precision | Found | Missed | Spurious |", "| --- | --- | --- | --- | --- | --- |"]
    for case_id, r in sorted(per_case.items()):
        L.append(f"| `{case_id}` | {r['recall']:.0%} | {r['precision']:.0%} | {r['true_positives']} | "
                 f"{r['false_negatives']} | {r['false_positives']} |")
    L.append("")

    misses = [(c, m) for c, r in sorted(per_case.items()) for m in r["missed"]]
    if misses:
        L += ["## Missed bugs", "",
              "Every line here is a lens that did not fire. This is the list to work from.", ""]
        L += [f"- `{c}`: {m}" for c, m in misses]
        L.append("")

    spurious = [(c, s) for c, r in sorted(per_case.items()) for s in r["spurious"]]
    if spurious:
        L += ["## False positives", "",
              "Each of these survived the verifier and all four gates and was still wrong. "
              "Read them before touching the confidence constants.", ""]
        L += [f"- `{c}`: {s}" for c, s in spurious]
        L.append("")

    args.out.write_text("\n".join(L), encoding="utf-8")

    payload = {"aggregate": agg, "per_case": per_case, "calibration": cal, "missing_runs": missing_runs}
    if args.json_out:
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Recall {agg['recall']:.1%} | Precision {agg['precision']:.1%} | F1 {agg['f1']:.3f} | "
          f"FP/case {agg['fp_per_case']} | cases {agg['cases']}")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
