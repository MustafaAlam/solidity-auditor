# Observability — a run you can explain afterwards

A multi-agent run is non-deterministic and mostly invisible. Without a trace, the
only thing anyone can say about a bad audit is "it missed things", which is not
an actionable statement. This phase makes each run answerable.

## The artifacts

Every run produces four files in `{bundle_dir}`, and they are the only durable
output besides the report:

| File | Written by | Purpose |
| --- | --- | --- |
| `system-map.json` | MAP | what the system believes the code is |
| `roster.json` | ROUTE | which agents ran and *why* |
| `ledger/*.jsonl` | HUNT | raw agent output, append-only |
| `run.json` | all phases | timing, status, cost, coverage, quorum |

`run.json` is written incrementally, not at the end. A run that dies at minute
thirty still leaves a diagnosable manifest — writing it only on success means it
exists exactly when it is not needed.

## The four questions a manifest must answer

1. **Why did this agent run?** → `agents[].spawn_reason`
2. **What did it see?** → `agents[].context_slice`, `slice_lines`
3. **Did it finish?** → `agents[].status`, `attempts`, `duration_s`
4. **What did it cost?** → `cost.*`, and `context_amplification` above all

## Context amplification — the number to watch

```
context_amplification = total input tokens / source tokens
```

This is the headline efficiency metric across versions. v2.7 sent full source to
a fixed 25 agents, so its amplification sat near 25×. v3 routes and slices, so a
mid-size protocol should land in the 5–10× range at `standard` budget.

Print it in the report appendix on every run. A version bump that raises it needs
a matching improvement in the eval scores to justify itself.

## Marker compliance

The Feynman / Socratic / Inversion markers in `senior-auditor-sop.md` only mean
something if someone counts them. Count them:

```bash
grep -c '\[Feynman:'   <agent transcript>
grep -c '\[Socratic:'  <agent transcript>
grep -c '\[Inversion:' <agent transcript>
```

Write the counts into `agents[].markers`. An agent that examined fourteen
functions and emitted two Feynman markers skimmed, and its findings should be
read as a skim. Marker counts near zero across the whole roster mean the SOP is
not landing — that is a prompt bug, and it is invisible without this count.

## What not to log

- Never log source code into the manifest. It bloats the artifact and duplicates
  the ledger.
- Never log the user's file paths outside the working directory.
- Never log API keys, RPC URLs with credentials, or `.env` contents. If the
  INGEST phase encounters a `.env`, record only that it exists.

## Retention

The bundle directory is transient build state. After the report is written:

- default → delete it
- `--keep-bundle` → retain and print the path
- run failed or was DEGRADED → **always** retain, and print the path, whether or
  not the flag was passed. A failed run's evidence is the one thing worth keeping.

## Cross-run comparison

Append one line per run to `.audit-history.jsonl` in the repo root (gitignored):

```json
{"run_id":"...","date":"2026-08-20","skill_version":"3.0.0","budget":"standard",
 "sloc":4200,"agents":14,"findings":{"critical":0,"high":2,"medium":5},
 "confirmed":6,"refuted":3,"amplification":7.1,"wall_clock_s":540}
```

Two weeks of these turn "did the audit get better?" from an opinion into a
diff — and they are what makes a regression in the eval corpus traceable to the
version that caused it.
