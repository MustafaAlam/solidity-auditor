# Changelog

## 3.0.0 — production multi-agent system

v2.7 was a strong hunting system with no engineering around it. The specialty
lenses, the SOP mental tools, the Devil's Advocate dimensions and the four
judging gates were good and are carried forward unchanged. Everything around
them was prose instructions to a model asked to be its own scheduler, parser,
deduplicator and statistician — and it had no way to know when it failed.

### The five structural changes

**1. Evidence-driven routing.** Agents spawn because the map found evidence for
them, not because the roster is a constant. A 300-line ERC-20 draws 6 agents; a
full lending protocol draws 22. Context slicing above 2000 SLOC. Every agent
records `spawn_reason`, so a roster is auditable and a missed bug is traceable
to a routing decision rather than a mystery.

Cost effect: v2.7 sent full source to a fixed 25 agents, so its context
amplification sat near 25×. A routed and sliced run should land in the 5–10×
range at `standard`.

**2. Strict JSON contract.** `schemas/finding.schema.json` replaces prose
findings. `validate_findings.py` is the only thing that can admit a record.
Malformed output gets one repair attempt then goes to quarantine — counted in
telemetry, never silently dropped. A high-severity claim too malformed to parse
is now stated in the summary rather than vanishing.

**3. Adversarial verifier loop.** A structurally separate critic with narrow
context tries to *kill* every medium-and-above candidate. Bounded iterations,
one repair pass on `WEAKENED`, PoC execution where the project compiles. The
strongest counter-argument is recorded and printed even on confirmed findings.

This is the false-positive fix. v2.7 asked each hunting agent to run its own
Devil's Advocate, which is a good instruction and not enough — an agent that has
just built a case for a bug is the worst available judge of it.

**4. Deterministic reduction.** Dedup, function isolation, wide description, fix
preservation, completeness accounting and axis coverage are now `reduce.py`
rather than sixty lines of prose the orchestrator executes by hand. Fix
distinctness is decided by hashing normalized added lines. Confidence is a
formula over proof strength, corroboration, PoC status, verifier verdict and
unbypassed DA blockers — not a convention. Severity is clamped by confidence, so
a "critical" the system privately doubts presents as a medium.

The model keeps exactly one job in this phase: adjudicating whether two
differently-tagged bug classes in one function are one defect or two.

**5. Reliability and observability.** Per-agent lifecycle with status, one retry
ever, quorum with DEGRADED reporting, abort below 50%, resumable runs, and a
`run.json` manifest carrying timing, cost, coverage and marker compliance.
Failure is now visible instead of indistinguishable from a clean result.

### New for the 2026 threat surface

Four specialties, all evidence-routed:

- `account-abstraction` — EIP-7702 delegate auth and signature scope, ERC-7201
  storage in delegated accounts, `chain_id = 0` replay, and the protocol-side
  breakage of `tx.origin == msg.sender` and `extcodesize` EOA checks.
- `proxy-upgrade` — uninitialized implementations, the CPIMP front-run
  middleman, slot collisions across versions, `__gap` arithmetic, UUPS bricking.
- `transient-storage` — EIP-1153 locks that never clear, transaction-scope reuse
  across multicalls, cross-`delegatecall` slot collisions, flash-accounting
  settlement.
- `crosschain-l2` — chain-id and peer binding, out-of-order delivery, sequencer
  uptime, block-time drift, forced-inclusion windows.

`judging.md` gained two safe patterns: correctly-cleared transient locks and
ERC-7201 namespaced storage. Both are the fix, not the bug.

### Eval harness

`evals/` adds a corpus manifest, ground-truth format, and `score.py` computing
recall, precision, F1, FP-per-case, severity agreement and confidence
calibration, with baseline diffing.

**No version bump without a before/after.** v2.7's changelog contains four
consecutive versions of unmeasured improvement; this is the mechanism that stops
that.

### Deployable service

`service/` is a runnable twin of the same graph — Pydantic schemas, the same
router, an append-only ledger, the verifier loop, A2A agent cards over FastAPI,
provider adapters for Anthropic / Vertex / OpenAI-compatible endpoints, Cloud
Run deployment, and an optional ADK composition.

44 tests, all offline against a stub provider. `tests/test_parity.py` asserts the
service and the skill's scripts produce identical confidence, grouping and fix
fingerprints — because if they drift, no scorecard from either is comparable.

### Carried forward unchanged

`senior-auditor-sop.md`, the 25 v2.7 specialty files, the six DA dimensions, the
four judging gates, the admin-amplifier rule, the safe-patterns list, the
architecture-first MAP, and the blocking human gate.

### Baseline

Not yet established. Build the corpus per `evals/README.md`, run it against
3.0.0, and freeze `baselines/v3.0.0.json`. Until that exists, every claim above
about precision and cost is a design intent rather than a measurement — and
saying so is the point of the harness.

---

## 2.7.0 and earlier

See the v2.7 skill for the prior changelog. Summary: 2.5.0 introduced the
architecture-first MAP, themed hunt lanes, the Devil's Advocate protocol, PoC
discipline and the axis-coverage gate; 2.6.0 added domain and proof-tooling
agents for a 25-agent roster; 2.7.0 grafted richer specialty text and the
admin-amplifier judging rule.
