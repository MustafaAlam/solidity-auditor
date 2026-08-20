# Reliability — failure is normal, silence is not

A 20-agent fan-out will have agents that time out, return prose instead of JSON,
crash mid-write, or return nothing. v2.7 had no answer for any of these: a dead
agent was indistinguishable from an agent that found nothing, and the report
looked identical either way.

The rule in v3: **every failure is visible in the output.** A degraded audit that
says so is useful. A degraded audit that looks complete is dangerous.

---

## 1. The ledger is append-only

Each agent writes to its own file: `{bundle_dir}/ledger/agent-<id>.jsonl`.

- One JSON object per line, flushed as produced.
- No agent ever writes another agent's file. No shared file, no lock, no lost
  update.
- A crash mid-run leaves every completed record intact — the ledger is the
  recovery point.

This is the "shared environment" of the multi-agent system, and making it
append-only per-writer is what makes concurrent agents safe without coordination.

---

## 2. Per-agent lifecycle

Register the agent in `run.json` **at spawn time** with `status: "spawned"`.
An agent that is never registered cannot be noticed missing.

| State | Trigger | Action |
| --- | --- | --- |
| `spawned` | task created | record model tier, slice, reason |
| `ok` | returned, all records valid | done |
| `retried-ok` | first attempt failed, retry succeeded | keep both attempt counts |
| `invalid-output` | returned, records failed schema | one repair attempt (below) |
| `timeout` | exceeded wall-clock budget | one retry at most, then give up |
| `quarantined` | repair also failed | records moved to `quarantine.jsonl` |
| `failed` | hard error, no output | recorded as a coverage gap |
| `skipped` | dropped by budget cap | recorded with reason |

**Retry policy: at most one retry per agent, ever.** Not three, not exponential
backoff. If a specialty agent fails twice, the failure is structural (bad slice,
context overflow, an impossible instruction) and retrying is spending money to
get the same answer. Record it as a coverage gap and move on.

---

## 3. Output repair — exactly one attempt

When an agent returns records that fail `validate_findings.py`:

1. Send the agent back its own invalid records plus the exact validator errors.
2. Ask only for corrected JSON. No new analysis, no new findings — a repair pass
   that discovers bugs is an agent gaming the retry.
3. Re-validate. Still failing → quarantine.

Quarantined records are **never** silently dropped. They are counted in the
report's telemetry appendix, and if any quarantined record claimed
`severity_claim >= high`, that fact is stated in the summary. A high-severity
claim that was too malformed to parse is exactly the thing a reader needs to
know about.

---

## 4. Timeouts

Set a per-agent wall-clock budget from the tier:

| Budget | Per-agent timeout |
| --- | --- |
| `quick` | 3 min |
| `standard` | 8 min |
| `deep` | 15 min |
| `exhaustive` | 30 min |

Do not poll for completion. Act on completion notifications, and treat the
timeout as a deadline enforced by the runtime where one exists. Where the runtime
cannot enforce a timeout, note it and rely on quorum instead.

---

## 5. Quorum

```
ratio = agents_returning_valid_output / agents_spawned
```

- `ratio >= 0.8` → normal report.
- `ratio < 0.8` → **DEGRADED**. The report carries a banner at the top naming
  every missing lane, and the completeness/coverage numbers are explicitly
  labelled partial.
- `ratio < 0.5` → do not produce a findings report at all. Emit a run summary
  explaining what failed and what to re-run. A half-dead fan-out produces a
  findings list whose absences look like clean bills of health, which is worse
  than no list.

Core agents are load-bearing: **if any always-on core agent failed, the run is
DEGRADED regardless of ratio.** Losing `access-control` on a 30-agent run still
leaves ratio at 0.97 and still means nobody checked the permission model.

---

## 6. Resumption

`run.json` plus the ledger is enough to resume. On restart with `--resume <dir>`:

1. Read `run.json`; every agent with `status: "ok"` or `"retried-ok"` is done.
2. Re-spawn only agents in `spawned`, `timeout`, `failed`, or `invalid-output`.
3. Skip MAP and ROUTE entirely — reuse `system-map.json` and `roster.json`.
4. Continue from VERIFY.

Resumption is what makes an expensive `exhaustive` run survivable. Without it, one
timeout at minute forty means paying for the whole fan-out again.

---

## 7. Never-do list

- Never let a parse failure delete a record. Quarantine, count, report.
- Never report coverage numbers computed over agents that did not run.
- Never retry more than once.
- Never let the report render if `completeness.ok` is false — that means the
  reducer lost a (contract, function) pair, which is a bug in the pipeline, not
  a finding about the code. Fail loudly.
- Never treat "no findings" and "no output" as the same thing.
