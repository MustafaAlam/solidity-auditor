# REPORT — rendered from data

The report is produced by `scripts/render_report.py` from `reduced.json` and
`run.json`. Structure, ordering, counts, completeness and telemetry are all
mechanical. Do not hand-write them, and do not re-read source to "double-check"
before writing — the agents did that, verification filtered it, and re-reading
costs minutes while almost never changing a verdict.

The model writes exactly one thing: the **summary prose**, passed in via
`--summary`.

---

## What the model writes

A short markdown file, 3–6 sentences, no headers, covering:

1. The systemic themes — the two or three patterns that explain most findings.
   "Fee rounding direction plus hook ordering after mutation" is a theme.
   "There were five medium findings" is a count, and the table already shows it.
2. What the audit did **not** cover, and why. Coverage gaps, skipped agents,
   quarantined records, degraded quorum.
3. The single thing to fix first, if one stands out.

Do not restate the severity counts. Do not congratulate the codebase. If nothing
systemic emerged, say the findings are unrelated — that is real information.

---

## Structure (produced automatically)

```
# <Protocol> — Security Audit Report
  version / run id / date / scope / budget / agents
  [DEGRADED banner, when quorum failed]

## Summary
  severity table
  Completeness: N unique (Contract, function) in raw → N covered in final
  AxisCoverage: X/Y hot-function-axis pairs covered; Z AXISGAP LEADs
  Verification: confirmed / weakened / refuted; PoCs passing / compiled
  <model-written themes paragraph>

## Findings           ordered by severity, then confidence
## Leads              survived gating, not promoted
## Coverage gaps      hot function × axis nobody examined
## Functions with multiple coexisting defects
## Rejected at gates
## Appendix — run telemetry
```

## Per-finding block

Location · bug class · group key · agents · axes · confidence · verifier verdict,
then Path, Mechanisms (or Root cause), Proof, Description, PoC, Fix(es),
**Strongest counter-argument considered**, Residual risk, Judging note.

Two details worth knowing about:

**Mechanisms.** When a group merged records with genuinely different root causes,
every one is listed. This is the v2.7 wide-description rule, now enforced by the
reducer instead of by discipline.

**Fixes.** One fix renders plainly. Two or more render as `Option A`, `Option B`,
verbatim from the agents that proposed them. Distinctness was decided by hashing
normalized added lines, so a fix cannot be lost to a paraphrase.

---

## Severity labels

| Label | Meaning |
| --- | --- |
| CRITICAL | direct fund loss or permanent DoS of a core path, permissionless |
| HIGH | fund loss behind a precondition; frozen trading; selective censorship; large economic leakage |
| MEDIUM | state inconsistency; non-atomic admin; gameable asymmetric branches |
| LOW | reachable but bounded; unlikely precondition |
| INFORMATIONAL | standards conformance, docs mismatch, hardening |

Assigned by `judging.md` and clamped by confidence. Never edited in the renderer.

---

## Hard rules

- **Never render if `completeness.ok` is false.** A lost (contract, function)
  pair is a pipeline bug. Fix it and re-reduce; do not ship a report that has
  already dropped evidence.
- **Always print the counter-argument**, including on confirmed findings. A
  reader who can see the best case against a finding can calibrate. One who sees
  only the case for it cannot.
- **Always print coverage gaps**, even when there are many. They are the honest
  boundary of the audit. Group them by function so the list stays readable.
- **Never omit `NOT-RUN` verification status.** "Not checked" and "checked and
  fine" must never look alike.
- The DEGRADED banner is not optional and goes above the summary, not in the
  appendix.

## File output

Printing to the conversation is the default. `--file-output` additionally writes
`audit-report-<YYYY-MM-DD>-<run_id>.md` to the working directory. Never write a
report file unless the flag was passed.
