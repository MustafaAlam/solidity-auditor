# Migrating from v2.7.0

Nothing you wrote is thrown away. The 25 specialty files, the SOP and the
judging gates carry forward. What changed is the machinery around them.

## 1. Drop in your existing agent files

Copy every `*-agent.md` from your v2.7 `references/hacking-agents/` into the same
directory here. They work unchanged — the router refers to them by `agent_id`,
which is the filename minus `-agent.md`.

This bundle ships the four new ones plus the rewritten `shared-rules.md`,
`senior-auditor-sop.md` and `adversarial-verifier-agent.md`. Everything else is
yours.

```
references/hacking-agents/
  senior-auditor-sop.md          ← replaced (v3, adds a note on the verifier)
  shared-rules.md                ← replaced (v3, JSON output contract)
  adversarial-verifier-agent.md  ← new
  account-abstraction-agent.md   ← new
  proxy-upgrade-agent.md         ← new
  transient-storage-agent.md     ← new
  crosschain-l2-agent.md         ← new
  access-control-agent.md        ← copy yours
  asymmetry-agent.md             ← copy yours
  ... all 25 from v2.7           ← copy yours
```

## 2. What moved

| v2.7 | v3.0.0 |
| --- | --- |
| `judging.md` | `references/orchestration/judging.md` (four gates unchanged, now applied to JSON) |
| `report-formatting.md` | `references/orchestration/report-formatting.md` + `scripts/render_report.py` |
| `senior-auditor-sop.md` | `references/hacking-agents/senior-auditor-sop.md` |
| dedup rules inside `SKILL.md` | `references/scripts/reduce.py` |
| agent prompt templates in `SKILL.md` | `references/orchestration/agent-prompt.md` |
| the fixed 25-agent bundle table | `references/orchestration/routing.md` |

## 3. The one breaking change

**Agents emit JSON Lines, not prose blocks.**

If you customized any specialty file's "Output fields" section, update it. The
old format:

```
FINDING | contract: Vault | function: withdraw | bug_class: reentrancy
path: ...
proof: ...
```

becomes one JSON object per line conforming to
`references/schemas/finding.schema.json`. `shared-rules.md` carries a full
worked example, and every specialty's extra fields (`seam`, `guard_gap`) are
already in the schema.

Most specialty files need no change at all — they describe *what to hunt*, and
only the trailing "Output fields" block mentions format.

## 4. New behaviour you will notice immediately

**Fewer agents on small code.** A 300-line contract now draws 6–8 agents instead
of 25. That is the routing working. `roster.json` and the printed roster explain
every choice; if a specialist you wanted is missing, the evidence flag that
should have triggered it did not fire in MAP.

**Findings can be rejected by the verifier.** A candidate that the adversarial
verifier refutes never reaches the report. Its `residual_risk` may survive as a
lead. If you think it was wrong, `verified.json` has the full verdict and the
counter-evidence.

**Confidence numbers changed.** They are computed now. A finding that was
"conf 90" by convention may compute to 65 if it has a sketch PoC, no
corroboration and was not verified. That is the formula working, not a
regression — and severity is clamped by it, so some old highs present as
mediums until a PoC or a second agent backs them.

**Reports carry counter-arguments.** Every finding prints the strongest case
against itself. This is intentional and it is the most useful line in most
findings.

**Runs can refuse to report.** Below 50% agent quorum, or with a core lane
missing, you get a run summary instead of a findings list.

## 5. Recommended first run

```bash
# 1. See what would run, before spending anything
/audit --budget quick <your usual target>

# 2. Compare against a v2.7 report you already trust
/audit --budget standard --keep-bundle <same target>
```

Then read three things in `{bundle_dir}`:

- `roster.json` — is the agent selection right for this codebase?
- `run.json` — did every agent return? what did it cost? marker counts sane?
- `reduced.json` `merge_queue` — did reduction ask you anything?

## 6. Before you tune anything

Build the eval corpus first. `references/evals/README.md` walks through it; 20
cases is enough to see a change.

The temptation after a first v3 run is to adjust the confidence constants or
loosen a gate because a finding you liked got demoted. Resist it until you can
measure the effect — that instinct is exactly how v2.7 accumulated four versions
of unverified changes.
