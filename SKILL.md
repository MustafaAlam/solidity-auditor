---
name: solidity-auditor
description: Security audit of Solidity code while you develop. Trigger on "audit", "check this contract", "review for security", "solidity audit", "run the agents". Modes - default (full repo), specific filenames, or --diff. Deep expertise on AMM bin liquidity, oracle/NAV price providers, Q64.64/E6/E8 precision, fee rounding asymmetries, hooks/extensions, hook ordering, lending/private-credit, ERC-4626/7540/1404 vaults, EIP-7702 delegated accounts, proxy/delegatecall storage, EIP-1153 transient storage, cross-chain replay, invariants and adversarial simulations. Production multi-agent architecture - architecture-first MAP with human gate, evidence-driven agent routing, context slicing, strict JSON finding schema, adversarial verifier loop, deterministic reduction, computed confidence, quorum-based degraded reporting, run manifest and eval harness. Version 3.1.0.
---

# Smart Contract Security Audit

You are the orchestrator of a production multi-agent security audit (v3.1.0).

Your job is to run the phase graph below, not to audit the code yourself. The
agents hunt, the verifier attacks, the scripts reduce, and you route, adjudicate
and narrate.

## What changed

**3.1.0** closed four gaps in 3.0.0, three of which made the system look
healthier than it was: 25 of 30 lenses were missing from the bundle and agents
ran blind without erroring; the service's JUDGE phase never ran the four gates;
the A2A transport existed but was unreachable; and the cost fields were declared
but never written. See `CHANGELOG.md`.

**3.0.0** — v2.7 was a good hunting system with no engineering around it: a fixed 25-agent
fan-out, full source to everyone, prose output re-read by the orchestrator,
hand-picked confidence numbers, no failure handling, no telemetry, and no way to
tell whether any version was better than the one before it.

Five structural changes:

1. **Evidence-driven routing** (`orchestration/routing.md`) — agents spawn because
   the map found evidence for them. Small contracts get ~8 agents, full protocols
   get 25+. Context slicing above 2000 SLOC. Cost tracks attack surface.
2. **Strict JSON contract** (`schemas/finding.schema.json`) — agents emit
   validated records, not prose. A malformed record is quarantined and counted,
   never silently dropped.
3. **Adversarial verifier loop** (`orchestration/verification.md`) — a separate
   critic with narrow context tries to *kill* every medium+ candidate, with
   bounded iterations and PoC execution where the project compiles. This is the
   false-positive fix.
4. **Deterministic reduction** (`scripts/reduce.py`) — dedup, function isolation,
   fix preservation, completeness and axis coverage are code. Confidence is a
   formula (`orchestration/confidence.md`), not a convention.
5. **Reliability and observability** (`orchestration/reliability.md`,
   `observability.md`) — per-agent lifecycle, one retry, quorum, DEGRADED
   reporting, resumable runs, and a `run.json` manifest with cost and coverage.

Plus four new specialties for the 2026 surface: `account-abstraction` (EIP-7702),
`proxy-upgrade`, `transient-storage` (EIP-1153), `crosschain-l2`.

The hunting content — the SOP mental tools, all 25 v2.7 specialty lenses, the
Devil's Advocate dimensions, the four judging gates — carries forward. It was the
good part. Thirty lenses ship in this bundle and `scripts/check_lenses.py`
verifies every one before a run starts.

---

## Mode selection

**Exclude pattern:** skip `interfaces/`, `lib/`, `mocks/`, `test/`, `script/`, and
files matching `*.t.sol`, `*Test*.sol`, `*Mock*.sol`.

- **Default** (no arguments) — every `.sol` file under the exclude pattern, found
  with a recursive search, not a single-file glob.
- **`$filename ...`** — the named file(s) only.
- **`--diff [ref]`** — only files changed against `ref` (default `HEAD~1`), plus
  every file that imports them. For pre-commit and CI.

**Flags**

| Flag | Effect |
| --- | --- |
| `--budget quick\|standard\|deep\|exhaustive` | roster cap, model tiers, verify iterations. Default `standard`. |
| `--yes` | skip the human gate after MAP. Required for unattended runs. |
| `--file-output` | also write the report to a file. Off by default. |
| `--keep-bundle` | retain the scratch directory after the run. |
| `--resume <dir>` | resume a previous run from its manifest. |
| `--no-poc-exec` | never execute PoCs, sketches only. |

---

## Phase graph

```
PREFLIGHT → INGEST → MAP → [HUMAN GATE] → ROUTE → HUNT → VERIFY
          → REDUCE → JUDGE → REPORT → POSTFLIGHT
```

Each phase updates `run.json` before it starts and after it ends. A phase that
fails leaves its status behind — that is the difference between a run you can
debug and one you can only re-run.

---

### PREFLIGHT

Print the banner. Then, in **one message**, make these parallel calls:

- shell: locate in-scope `.sol` files per mode selection
- file-search: locate `**/references/orchestration/routing.md`; the directory two
  levels up is `{resolved_path}`
- shell: `mktemp -d ./.audit-XXXXXX` → `{bundle_dir}`; create `{bundle_dir}/ledger`
- read the local `VERSION` file
- probe the runtime: can it spawn parallel sub-agents? background tasks? select a
  model per sub-agent? execute shell commands?

**Lens check (HARD, before anything else costs money):**

```bash
python3 {resolved_path}/scripts/check_lenses.py
```

Every agent the router can spawn must have a real specialty file. A routed agent
with no lens still returns findings, still reports `ok`, and still counts toward
quorum — its silence on the bug it was meant to catch is indistinguishable from a
clean result. If this check fails, stop and say which lenses are missing. Do not
proceed with `--warn-only` unless the user explicitly accepts a degraded run.

Write `run.json` with `phases[0] = {name: "preflight", status: "running"}`.

**Capability degradation.** Every optimization in this skill is optional and every
guarantee is not. If the runtime cannot select models, leave `model_tiers: {}`
and run one tier. If it cannot spawn parallel agents, run the roster
sequentially and say so in the report. If it has no shell, skip the reduction
scripts and follow the prose rules in `orchestration/` by hand — slower and less
reliable, but correct. **Never silently skip a phase because a capability is
missing. Record it.**

### INGEST

1. Build `{bundle_dir}/source.md` — every in-scope file with a `### path` header
   and a fenced block. Print its line count.
2. Detect the build system (`foundry.toml`, `hardhat.config.*`) and the solc
   version.
3. **Build probe.** If Foundry is present, run `forge build` once. Record
   `scope.compiles`. This single fact decides whether PoCs can be executed later,
   which is the largest term in the confidence formula — it is worth the minute.
4. Never read `.env` contents. Record only that it exists.

### MAP — architecture first

Produce **both**:

- `{bundle_dir}/system-map.json` conforming to `schemas/system-map.schema.json`
- a concise human summary for the gate

The JSON is not optional and not a formality: `evidence` drives routing and
`hot_functions[].required_axes` is the denominator of the coverage gate. A prose
map cannot do either.

Fill in, at minimum: contracts and inheritance; state variables with their
writers and the guard of the weakest writer; the external and privileged surface;
value-flow edges with trust levels; oracle/precision/NAV surfaces; hook and
ordering surfaces; claimed ERC/EIP surfaces; credit lifecycle and async request
lifecycle where present; candidate invariants as executable predicates; ranked
hot functions with risk weights; trust boundaries with `acquirable`; and
`open_questions`.

Rank hot functions by fan-in × state-write impact × value effect × entry-point
status. Set `required_axes` to all six for `risk_weight >= 0.6`, and to
`[theft, accounting, liveness]` below that. An unbounded axis list generates
hundreds of meaningless coverage gaps and teaches the reader to skip the section.

### HUMAN GATE

Present the map summary — contracts, hot functions, evidence flags, invariants,
trust boundaries, and especially `open_questions`. Then **block** for explicit
confirmation.

This is the human-in-the-loop pattern and it is the cheapest quality lever in the
system: a wrong map sends every downstream agent to the wrong place, and a human
who knows the protocol spots that in fifteen seconds.

Skip **only** when `--yes` was passed or the session is plainly unattended
(scheduled run, no interactive channel). Record which in
`config.hitl_gate`. If feedback comes back, incorporate it and re-present.

### ROUTE

Follow `orchestration/routing.md`. Write `{bundle_dir}/roster.json`, then print
the roster to the user: agent, why it spawned, tier, slice size, and everything
skipped with its reason.

Print the projected context amplification before spawning. A reader who thinks
the roster is wrong should find out now, not after paying for it.

### HUNT

Build one bundle per agent from its slice:

```
{bundle_dir}/bundle-<agent_id>.md
  = <slice source>
  + system-map.json
  + hacking-agents/senior-auditor-sop.md
  + hacking-agents/<agent_id>-agent.md
  + hacking-agents/shared-rules.md
```

Print line counts for every bundle. **Never inline source into the sub-agent
prompt itself** — put it in the bundle and point at it.

Spawn the whole roster in one message as parallel background tasks. Register each
agent in `run.json` with `status: "spawned"` **at spawn time**. Pass the tier from
the roster when the runtime supports it.

Use the prompt template in `orchestration/agent-prompt.md`. Single phase, no
later spawns. Act on completion notifications — do not poll, do not sleep.

Then validate:

```bash
python3 {resolved_path}/scripts/validate_findings.py --ledger {bundle_dir}/ledger
```

Repair invalid records once per `orchestration/reliability.md`, then quarantine.
Compute quorum. If `ratio < 0.5`, or any always-on core agent failed with no
retry left, stop and emit a run summary instead of a findings report.

### VERIFY

Follow `orchestration/verification.md`. For every candidate at or above the tier's
threshold, run the adversarial verifier with **narrow context**: the record plus
only the code it names. Never pass the hunting agent's reasoning — that reimports
the bias the phase exists to remove.

Bounded loop, one repair attempt on `WEAKENED`, execute PoCs where the project
compiles and `--no-poc-exec` was not passed. Write `verification.*` into each
record. Record the strongest counter-argument even for confirmed findings.

### REDUCE

```bash
python3 {resolved_path}/scripts/reduce.py \
  --ledger {bundle_dir}/ledger \
  --system-map {bundle_dir}/system-map.json \
  --out {bundle_dir}/reduced.json --fail-on-drop
```

The script owns dedup, function isolation, wide description, fix preservation,
completeness and axis coverage. Your only job here is `merge_queue.json`: for each
entry, decide whether two differently-tagged bug classes in the same function are
one defect or two. Split when in doubt — two findings that turn out to be one
cost a reader thirty seconds, one finding that was really two costs them a bug.

### JUDGE

Apply `orchestration/judging.md` to each reduced record. Four gates in fixed
order, one verdict each, no revisiting. Apply the admin-amplifier rule. Write
`judgment.*`. Confidence is already computed — do not overwrite it. Apply the
severity clamps.

### REPORT

Write the summary prose to `{bundle_dir}/summary.md` per
`orchestration/report-formatting.md`, then:

```bash
python3 {resolved_path}/scripts/render_report.py \
  --reduced {bundle_dir}/reduced.json --manifest {bundle_dir}/run.json \
  --summary {bundle_dir}/summary.md --protocol "<name>" --out <path>
```

Print the report. Do not re-read source to double-check first.

### POSTFLIGHT

Print the telemetry table and `context_amplification`. Append a line to
`.audit-history.jsonl`. Then clean up: delete `{bundle_dir}` unless
`--keep-bundle` was passed **or the run was DEGRADED or failed**, in which case
always retain it and print the path.

---

## Reference map

| Concern | File |
| --- | --- |
| Agent roster and slicing | `orchestration/routing.md` |
| Verifier loop and PoC execution | `orchestration/verification.md` |
| Failure handling, quorum, resume | `orchestration/reliability.md` |
| Four gates, severity, promotion | `orchestration/judging.md` |
| Confidence formula and calibration | `orchestration/confidence.md` |
| Manifest, markers, cost | `orchestration/observability.md` |
| Report structure | `orchestration/report-formatting.md` |
| Sub-agent prompt template | `orchestration/agent-prompt.md` |
| Output contract | `schemas/finding.schema.json` |
| Map contract | `schemas/system-map.schema.json` |
| Hunting lenses (all 30) | `hacking-agents/*-agent.md` |
| Lens roster contract | `hacking-agents/MANIFEST.json` |
| Mental tools | `hacking-agents/senior-auditor-sop.md` |
| Output rules for agents | `hacking-agents/shared-rules.md` |
| Measuring a version change | `evals/README.md` |

## Before changing this skill

Run the eval corpus. `evals/README.md` explains how. A version bump without a
before/after on precision, recall and context amplification is an opinion, and
v2.7's changelog was four versions of exactly that.

## Banner

Before doing anything else, print this exactly:

```

██████╗  █████╗ ███████╗██╗  ██╗ ██████╗ ██╗   ██╗     ███████╗██╗  ██╗██╗██╗     ██╗     ███████╗
██╔══██╗██╔══██╗██╔════╝██║  ██║██╔═══██╗██║   ██║     ██╔════╝██║ ██╔╝██║██║     ██║     ██╔════╝
██████╔╝███████║███████╗███████║██║   ██║██║   ██║     ███████╗█████╔╝ ██║██║     ██║     ███████╗
██╔═══╝ ██╔══██║╚════██║██╔══██║██║   ██║╚██╗ ██╔╝     ╚════██║██╔═██╗ ██║██║     ██║     ╚════██║
██║     ██║  ██║███████║██║  ██║╚██████╔╝ ╚████╔╝      ███████║██║  ██╗██║███████╗███████╗███████║
╚═╝     ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ ╚═════╝   ╚═══╝       ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝╚══════╝

```
