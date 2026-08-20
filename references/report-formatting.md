# Report Formatting

Final output of Turn 4 must follow this exact structure. No intermediate dedup lists. Go straight to the report.

## Header

```
# Metric / <Protocol> Security Audit Report
**Skill version:** <VERSION>
**Date:** <YYYY-MM-DD>
**Scope:** <list of in-scope contracts or "full repo excluding interfaces/lib/mocks/test">
**Agents run:** 15 (11 single-specialty including dedicated hook-ordering + 4 gap-hunters)
**Completeness:** N unique (Contract, function) in raw → N covered in final
```

## Summary box (top of report)

- Critical: X
- High: Y
- Medium: Z
- Low / Informational: W
- Leads retained: V

Then a short paragraph of the most important systemic themes (e.g. "fee rounding direction + hook ordering after mutation + oracle hard-dependency").

## Findings (numbered, ordered by severity then by exploitability)

For each finding:

```
### N. [SEVERITY] Short title
**Contracts / Functions:** `Contract.function`, `Other.function`
**Bug class:** kebab-tag
**Group key:** Contract | function | bug-class
**Agents:** [1, 5, 11]
**Confidence:** 90 (or 75 for promoted LEAD)

**Path:**
caller → function → state change → impact

**Root cause:**
one sentence, code-level

**Proof:**
concrete numbers / traces / quoted lines. Must be sufficient to reproduce without the original agent notes.

**Description:**
1–3 sentences. Impact on users / LPs / protocol solvency / MEV.

**Fix:**
If single fix:
```diff
- old
+ new
```
or one-sentence suggestion if a full diff is not natural.

If ≥2 distinct fixes (HARD GATE from Turn 4):
**Fix (Option A — <label>)**:
```diff
...
```
**Fix (Option B — <label>)**:
```diff
...
```

**Notes / Chain:** (optional) if this finding feeds another, write `Chain: [N] + [M]`.
```

Severity labels: CRITICAL (direct fund loss or permanent DoS of core path), HIGH (griefing that freezes trading / selective censorship / large economic leakage), MEDIUM (state inconsistency / non-atomic admin / asymmetric branches that can be gamed), LOW (theoretical, hard to reach, or pure info).

## Leads section (after all findings)

Only LEADs that survived gating and were not promoted. Keep them short.

## Appendix (optional)

- Marker compliance summary (if the orchestrator grepped)
- Files scanned
- Bundle line counts

## Rules for the writer of the final report

- Never re-read source "just to double-check" after agents finished. Agents + dedup already did the work.
- Preserve every distinct mechanism and every distinct fix. No silent dropping.
- Wide-description when multiple mechanisms share a group_key: list them all under one heading.
- Function isolation is HARD: never merge findings that live in different functions.
- Completeness line is mandatory and must be accurate.
- After the report is printed (and optional `--file-output` write), delete the scratch bundle dir.
