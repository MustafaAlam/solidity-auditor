# ROUTE — Coordinator / Dispatcher

The router turns the SystemMapArtifact into a concrete roster: which agents run,
on which model tier, against which slice of source, with what token budget.

v2.7 spawned a fixed 25 agents on every run and gave each one the entire source.
A 300-line ERC-20 got a lending expert, an async-vault strategist and a
hook-ordering agent, all reading code that contains none of those things. That is
where most of the cost went and where most of the noise came from.

v3 spawns agents because the map found evidence for them. On a small token the
roster collapses to ~8 agents; on a full lending protocol it expands past 25.
Same skill, cost proportional to the attack surface.

---

## 1. Always-on core

These run on every audit regardless of evidence. They are the lanes that apply to
any Solidity contract, and they are the reason a routed run never under-covers.

| agent_id | lane | role |
| --- | --- | --- |
| `access-control` | callback-liveness | permission model, init, escalation |
| `math-precision` | semantic-consistency | rounding, scale, truncation |
| `invariant` | accounting-entitlement | properties that must hold |
| `execution-trace` | callback-liveness | state-vs-call ordering, reentrancy paths |
| `first-principles` | semantic-consistency | what is this actually supposed to do |
| `boundary` | adversarial-deep | zero, max, empty, single-element |

**Six agents is the floor.** Below that the audit is not an audit.

---

## 2. Evidence-triggered specialists

Each row fires only when the named field in `system_map.evidence` is truthy.
`spawn_reason` in the run manifest MUST record which trigger fired — a roster
nobody can explain is a roster nobody can tune.

| Trigger (evidence field) | Spawns | Why |
| --- | --- | --- |
| `has_oracle` | `oracle-expert`, `signature-trust` | price provenance, staleness, NAV |
| `has_lending` | `lending-expert`, `economic-security` | accrual, LTV, liquidation, waterfall |
| `has_vault` or `has_async_request_lifecycle` | `yield-strategist`, `numerical-gap` | share math, request lifecycle, idle vs reserved |
| `has_amm` | `economic-security`, `asymmetry`, `periphery` | fee direction, bin/tick math, router trust |
| `has_hooks` | `hook-ordering`, `flow-gap` | ordering after mutation, callback trust |
| `has_signatures` | `signature-trust`, `signature-gap` | nonce, domain separator, deadline, malleability |
| `erc_surfaces` non-empty | `erc-implementer` | claimed-vs-actual standard conformance |
| `eip_surfaces` non-empty | `eip-expert` | EIP semantics the code says it implements |
| `has_delegatecall` or `has_proxy_or_upgrade` | `proxy-upgrade` | storage collision, uninit impl, CPIMP |
| `has_account_abstraction` or `EIP7702` in `eip_surfaces` | `account-abstraction` | delegate auth, EOA assumptions, 4337 validation |
| `has_transient_storage` or `EIP1153` in `eip_surfaces` | `transient-storage` | tstore lifetime, locks that never clear |
| `has_crosschain` | `crosschain-l2` | replay across chains, sequencer, finality |
| `has_tokenomics` | `tokenomics-analyst` | emissions, vesting, reward accounting |
| `has_governance_timelock` | `trust-gap` | timelock bypass, semi-trusted roles |
| `has_fixed_point` or `has_fee_math` | `numerical-gap`, `asymmetry` | scale mismatch, fee direction |
| `has_assembly` | `boundary`, `proxy-upgrade` | memory safety, slot arithmetic |
| `has_nft_identity` | `trust-gap`, `erc-implementer` | identity-as-ownership confusion |

**Deduplicate the roster.** An agent triggered by three different rows still runs
once; record all three reasons.

---

## 3. Gap-hunters

Gap-hunters find bugs at the seam between two lenses, so they are only worth
spawning when both lenses are actually present.

| agent_id | Spawn when |
| --- | --- |
| `numerical-gap` | at least two of `has_fixed_point`, `has_fee_math`, `has_vault`, `has_amm` |
| `flow-gap` | `has_hooks` or ≥2 contracts with cross-contract value edges |
| `trust-gap` | ≥1 semi-trusted actor in `trust_boundaries` |
| `signature-gap` | `has_signatures` and (`has_oracle` or `has_crosschain`) |

---

## 4. Proof tooling

| agent_id | Spawn when | Notes |
| --- | --- | --- |
| `poc-writer` | any candidate reaches severity_claim ≥ medium | Runs in VERIFY, not HUNT |
| `invariant-tester` | `invariants` has ≥3 entries | Emits harness gaps as LEADs |
| `fuzzer` | budget ≥ `deep` and `scope.compiles` | Property/campaign design |
| `formal-verifier` | budget = `exhaustive`, or `has_lending`/`has_vault` at budget ≥ `deep` | Ghosts-first, CVL/Halmos sketches |

`adversarial-verifier` is **not optional and not routed** — see `verification.md`.

---

## 5. Budget tiers

`--budget` caps the roster and picks model tiers. Default is `standard`.

| Tier | Max agents | Model tiers | Verify iterations | Typical use |
| --- | --- | --- | --- | --- |
| `quick` | 8 | all `triage` | 1, medium+ only | pre-commit, CI on a diff |
| `standard` | 22 | core+specialists `deep`, rest `triage` | 2, medium+ | default development audit |
| `deep` | 28 | all `deep`, verifier `verify` | 2, all findings | pre-release |
| `exhaustive` | unlimited | all `deep`, verifier `verify` | 3, all findings + leads | pre-mainnet, contest prep |

The cap is a spend ceiling, not a target. Routing has already sized the roster to
the attack surface, so a plain ERC-20 draws six agents at every tier and the cap
only bites on genuinely broad protocols — where twenty agents is the correct
answer rather than an extravagant one.

When evidence does trigger more agents than the cap allows, drop from the bottom
of this priority order: **proof tooling → gap-hunters → domain specialists →
platform specialists → core.**

Platform specialists outrank domain specialists deliberately. A domain agent's
lens overlaps heavily with the core lanes — `economic-security` and `asymmetry`
share ground, and `access-control` already reads every guard. Platform agents
cover surfaces nothing else looks at: drop `proxy-upgrade` and nobody checks
storage collisions at all. Overlap is what makes an agent safe to drop;
uniqueness is what makes it expensive to lose.

Never drop core. Record every dropped agent in `run.json` as `status: "skipped"`
with its reason — an audit that quietly skipped the oracle expert must say so.

If the runtime cannot select models, leave tiers unset and note
`model_tiers: {}` in the manifest. Tiering is an optimization, never a
prerequisite.

---

## 6. Context slicing — the cost lever

Anthropic's multi-agent research found token volume explains roughly 80% of
performance variance, and that multi-agent systems burn on the order of 15× the
tokens of a single chat. Sending full source to every agent multiplies input by
the roster size for no benefit to an agent that only reads two files.

**Rule: slice when `total_sloc > 2000`. Below that, ship full source to everyone**
— the slicing overhead and the risk of hiding cross-file context outweigh the
savings.

Three slice kinds:

| Slice | Contents | Given to |
| --- | --- | --- |
| `full` | every in-scope file | `first-principles`, `invariant`, `execution-trace`, `access-control` |
| `domain` | files matching the domain's evidence + their direct imports + every file writing the same state vars | domain specialists, platform specialists |
| `hot` | the top-N hot functions' enclosing contracts + their direct dependencies | gap-hunters, proof tooling |

Every slice ships with the **full SystemMapArtifact** regardless of size. The map
is what lets a sliced agent know what it cannot see. A sliced agent that needs a
file outside its slice reads it directly — slicing narrows the default, it never
forbids a lookup.

Record `context_slice` and `slice_lines` per agent in the manifest, and compute
`context_amplification = total input tokens / source tokens` in `cost`. That
number is the headline cost metric across versions: v2.7 sat near the agent
count, v3 should land well under it.

---

## 7. Router output

Write `{bundle_dir}/roster.json` before spawning anything:

```json
{
  "budget": "standard",
  "slicing_enabled": true,
  "agents": [
    {
      "agent_id": "oracle-expert",
      "role": "domain",
      "spawn_reason": "evidence.has_oracle",
      "model_tier": "deep",
      "context_slice": "domain",
      "slice_files": ["src/PriceFeed.sol", "src/NavProvider.sol"],
      "slice_lines": 812
    }
  ],
  "skipped": [
    { "agent_id": "fuzzer", "reason": "budget=standard, requires deep" }
  ]
}
```

The roster is printed to the user before HUNT begins. A reader who disagrees with
the roster can stop the run at that point rather than after paying for it.
