#!/usr/bin/env python3
"""
render_report.py — render the final report from data, not from memory.

Why this exists
---------------
In v2.7 the report was written by a model recalling what 25 agents had said,
tens of thousands of tokens earlier. Anything it forgot was simply gone, and
nothing could detect that. Here the report is a pure function of
`reduced.json` + `run.json`, so "silent drop" stops being a failure mode: if a
record exists it is rendered, and the completeness line is computed rather
than asserted.

The model still writes the *prose* - the executive summary and the systemic
themes paragraph - because that genuinely needs judgment. It is injected via
--summary. Everything structural is mechanical.

Usage
-----
  python3 render_report.py --reduced .audit-x/reduced.json \
                           --manifest .audit-x/run.json \
                           --summary .audit-x/summary.md \
                           --out report.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

SEVERITY_ORDER = ["critical", "high", "medium", "low", "informational"]
OPTION_LABELS = "ABCDEFGH"


def sev_of(rec: dict) -> str:
    j = rec.get("judgment") or {}
    return j.get("final_severity") or rec.get("severity_claim") or "informational"


def render_fixes(fixes: list[dict]) -> str:
    """One fix renders plainly; two or more render as labelled options, verbatim.

    This is the v2.7 "fix preservation HARD GATE" - but the distinctness call
    was already made mechanically in reduce.py, so there is nothing here to
    get wrong.
    """
    if not fixes:
        return "_No fix proposed._\n"
    if len(fixes) == 1:
        f = fixes[0]
        out = f"**Fix ({f.get('label', 'change')}):** {f.get('summary', '')}\n"
        if f.get("diff"):
            out += f"\n```diff\n{f['diff'].rstrip()}\n```\n"
        return out
    out = ""
    for i, f in enumerate(fixes):
        tag = OPTION_LABELS[i] if i < len(OPTION_LABELS) else str(i + 1)
        out += f"\n**Fix (Option {tag} — {f.get('label', 'change')}):** {f.get('summary', '')}\n"
        if f.get("diff"):
            out += f"\n```diff\n{f['diff'].rstrip()}\n```\n"
    return out


def render_finding(n: int, rec: dict) -> str:
    j = rec.get("judgment") or {}
    v = rec.get("verification") or {}
    sev = sev_of(rec).upper()
    title = (rec.get("root_cause") or rec.get("description") or "")[:110].rstrip(". ")

    out = [f"### {n}. [{sev}] {title}", ""]
    loc = f"`{rec['contract']}.{rec['function']}`"
    if rec.get("file"):
        loc += f" — `{rec['file']}`" + (f":{rec['lines'][0]}" if rec.get("lines") else "")
    out += [
        f"**Location:** {loc}  ",
        f"**Bug class:** `{rec.get('bug_class')}`  ",
        f"**Group key:** `{rec.get('group_key')}`  ",
        f"**Agents:** {', '.join(rec.get('corroboration') or [rec.get('agent_id', '?')])} "
        f"({rec.get('agent_count', 1)} independent)  ",
        f"**Axes:** {', '.join(rec.get('axes') or [])}  ",
        f"**Confidence:** {j.get('confidence', '—')}  ",
    ]
    if v.get("verifier_verdict"):
        out.append(f"**Adversarial verifier:** {v['verifier_verdict']}  ")
    out.append("")

    out += ["**Path:**", "", rec.get("path", "—"), ""]

    mechs = rec.get("mechanisms") or []
    if len(mechs) > 1:
        out += ["**Mechanisms (all distinct causes in this group):**", ""]
        for m in mechs:
            out.append(f"- _{m.get('bug_class')}_ ({m.get('agent_id')}): {m.get('root_cause')}")
        out.append("")
    elif rec.get("root_cause"):
        out += ["**Root cause:**", "", rec["root_cause"], ""]

    proof = rec.get("proof") or {}
    if proof.get("content"):
        out += [f"**Proof ({proof.get('kind', 'evidence')}):**", "", "```", proof["content"].rstrip(), "```", ""]

    out += ["**Description:**", "", rec.get("description", "—"), ""]

    poc = rec.get("poc") or {}
    if poc.get("code"):
        out += [f"**PoC ({poc.get('status')}, {poc.get('framework', 'foundry')}):**", "",
                "```solidity", poc["code"].rstrip(), "```", ""]
        if poc.get("command"):
            out.append(f"Run: `{poc['command']}`")
        if poc.get("result"):
            out.append(f"Result: {poc['result']}")
        out.append("")
    elif poc.get("why_not"):
        out += [f"**PoC:** not feasible in this pass — {poc['why_not']}", ""]

    out += [render_fixes(rec.get("fixes") or [])]

    # The refutation is printed even when the finding survives. A reader who
    # can see the strongest counter-argument can calibrate; one who only sees
    # the claim cannot.
    if v.get("refutation"):
        out += [f"**Strongest counter-argument considered:** {v['refutation']}", ""]
    if v.get("residual_risk"):
        out += [f"**Residual risk:** {v['residual_risk']}", ""]
    if j.get("rationale"):
        out += [f"**Judging note:** {j['rationale']}", ""]

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the audit report from reduced findings.")
    ap.add_argument("--reduced", type=pathlib.Path, required=True)
    ap.add_argument("--manifest", type=pathlib.Path)
    ap.add_argument("--summary", type=pathlib.Path, help="Markdown file with the model-written themes paragraph.")
    ap.add_argument("--protocol", default="Protocol")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args()

    data = json.loads(args.reduced.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest and args.manifest.exists() else {}

    records = data.get("reduced", [])
    findings = [r for r in records if sev_of(r) != "rejected" and r.get("kind") == "FINDING"]
    leads = [r for r in records if sev_of(r) != "rejected" and r.get("kind") == "LEAD"]
    rejected = [r for r in records if sev_of(r) == "rejected"]
    gaps = data.get("axis_gaps", [])

    findings.sort(key=lambda r: (SEVERITY_ORDER.index(sev_of(r)), -int((r.get("judgment") or {}).get("confidence", 0))))
    counts = {s: sum(1 for r in findings if sev_of(r) == s) for s in SEVERITY_ORDER}

    cfg = manifest.get("config", {})
    quorum = manifest.get("quorum", {})
    cov = data.get("coverage", {})
    comp = data.get("completeness", {})
    ver = manifest.get("verification", {})
    cost = manifest.get("cost", {})

    L: list[str] = []
    L += [f"# {args.protocol} — Security Audit Report", ""]
    L += [
        f"**Skill version:** {manifest.get('skill_version', '3.0.0')}  ",
        f"**Run id:** `{manifest.get('run_id', '—')}`  ",
        f"**Date:** {dt.date.today().isoformat()}  ",
        f"**Scope:** {', '.join(cfg.get('targets') or ['full repo excluding interfaces/, lib/, mocks/, test/'])}  ",
        f"**Budget tier:** {cfg.get('budget', '—')}  ",
        f"**Agents run:** {len(manifest.get('agents', []))} "
        f"({quorum.get('returned_valid', '—')}/{quorum.get('expected_agents', '—')} returned valid output)  ",
    ]
    if quorum.get("degraded"):
        L += ["", "> ⚠️ **DEGRADED RUN.** Quorum was not met. Missing lanes: "
              f"{', '.join(quorum.get('missing_lanes') or [])}. "
              "Treat coverage claims below as partial and re-run the missing lanes before relying on this report.", ""]
    L.append("")

    L += ["## Summary", ""]
    L += ["| Severity | Count |", "| --- | --- |"]
    for s in SEVERITY_ORDER:
        L.append(f"| {s.capitalize()} | {counts[s]} |")
    L += [f"| Leads retained | {len(leads)} |", f"| Coverage gaps (AXISGAP) | {len(gaps)} |",
          f"| Rejected at gates | {len(rejected)} |", ""]

    L += [
        f"**Completeness:** {comp.get('unique_contract_function_raw', 0)} unique (Contract, function) in raw → "
        f"{comp.get('unique_contract_function_final', 0)} covered in final"
        + ("" if comp.get("ok", True) else f" — ⚠️ DROPPED: {', '.join(comp.get('dropped', []))}") + "  ",
        f"**AxisCoverage:** {cov.get('covered', 0)}/{cov.get('hot_function_axis_pairs', 0)} hot-function-axis pairs covered; "
        f"{cov.get('axisgap_leads', 0)} AXISGAP LEADs generated  ",
    ]
    if ver:
        L.append(
            f"**Verification:** {ver.get('confirmed', 0)} confirmed / {ver.get('weakened', 0)} weakened / "
            f"{ver.get('refuted', 0)} refuted of {ver.get('candidates_sent', 0)} sent; "
            f"{ver.get('poc_passing', 0)} PoCs passing, {ver.get('poc_compiled', 0)} compiled  "
        )
    L.append("")

    if args.summary and args.summary.exists():
        L += [args.summary.read_text(encoding="utf-8").strip(), ""]

    L += ["## Findings", ""]
    if not findings:
        L += ["_No findings survived gating._", ""]
    for i, rec in enumerate(findings, 1):
        L += [render_finding(i, rec), "---", ""]

    if leads:
        L += ["## Leads (unproven, retained for follow-up)", ""]
        for rec in leads:
            j = rec.get("judgment") or {}
            L.append(
                f"- **`{rec['contract']}.{rec['function']}`** (`{rec.get('bug_class')}`, conf {j.get('confidence', '—')}, "
                f"{rec.get('agent_count', 1)} agent(s)): {rec.get('description')}"
            )
        L.append("")

    if gaps:
        L += ["## Coverage gaps", "",
              "Hot functions no agent examined under the named risk axis. Not defects — unfinished work.", ""]
        by_fn: dict[str, list[str]] = {}
        for g in gaps:
            by_fn.setdefault(f"{g['contract']}.{g['function']}", []).append(g["bug_class"].replace("axisgap-", ""))
        for fn, axes in sorted(by_fn.items()):
            L.append(f"- `{fn}` — uncovered axes: {', '.join(sorted(axes))}")
        L.append("")

    if data.get("coexisting"):
        L += ["## Functions with multiple coexisting defects", ""]
        for c in data["coexisting"]:
            L.append(f"- `{c['contract']}.{c['function']}`: {', '.join(c['group_keys'])}")
        L.append("")

    if rejected:
        L += ["## Rejected at gates", ""]
        for rec in rejected:
            j = rec.get("judgment") or {}
            L.append(f"- `{rec['contract']}.{rec['function']}` (`{rec.get('bug_class')}`): {j.get('rationale', 'failed gating')}")
        L.append("")

    # Appendix: the run's own vital signs. Cheap to print, and the first thing
    # anyone re-running the audit will want.
    L += ["## Appendix — run telemetry", ""]
    if manifest.get("agents"):
        L += ["| Agent | Role | Tier | Status | Records | Slice lines | Time (s) |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for a in manifest["agents"]:
            L.append(
                f"| {a.get('agent_id')} | {a.get('role', '—')} | {a.get('model_tier', '—')} | {a.get('status')} | "
                f"{a.get('records_valid', 0)} | {a.get('slice_lines', '—')} | {a.get('duration_s', '—')} |"
            )
        L.append("")
    if cost:
        L += [
            f"- Input tokens: {cost.get('input_tokens', '—')}  ",
            f"- Output tokens: {cost.get('output_tokens', '—')}  ",
            f"- Context amplification: {cost.get('context_amplification', '—')}×  ",
            f"- Wall clock: {cost.get('wall_clock_s', '—')} s  ",
            "",
        ]

    args.out.write_text("\n".join(L), encoding="utf-8")
    print(f"Wrote {args.out} — {len(findings)} findings, {len(leads)} leads, {len(gaps)} coverage gaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
