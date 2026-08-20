# Shared Scan Rules (v3)

## Bundle contents

Your bundle is: your source slice, the SystemMapArtifact, the SOP (HOW to think),
your specialty agent (WHAT to look for), and these shared rules (output contract
and the mandatory mental-tool protocol).

Read the whole bundle once at the start. Use Read/Grep only for cross-file
investigation or context outside your slice — do not re-read slice files for the
initial scan.

**Your slice may not be the whole system.** The system map lists every contract,
including ones you cannot see. When a value flows in from outside your slice,
read that file rather than guessing at it. Slicing narrows your default; it never
forbids a lookup.

**The protocol below applies continuously during source reading — not just before
it.** Reading source does not turn the protocol off; every trigger fires the
moment it occurs, throughout the review.

When matching function names, check both `functionName` and `_functionName`.

---

## Mental tool protocol — MANDATORY

The three tools in `senior-auditor-sop.md` are not optional. Each has a trigger.
**When the trigger fires, emit the marker in your response text before
continuing.** Markers live in your working text — they never go into the ledger
file.

| Trigger | Required marker | Content |
| --- | --- | --- |
| You open a new function or contract | `[Feynman: <name>]` | Explain it in plain English. No `mload`, `assembly`, `mstore`, `safeTransfer`, `mulDiv`, `require`, `msg.sender`, storage slots. Keep going until the explanation is solid. Where your wording slips back to jargon, you are papering over an assumption — mark that spot. That is where bugs hide. |
| You stop on a line whose purpose is not immediately clear | `[Socratic: <file:line> — why?]` | One question that drills past "because that's how it's written". If your answer restates the code, ask again. Stop when the answer exposes the implicit belief the code rests on. |
| A path reads as clean / a check looks sufficient / a guard looks correct | `[Inversion: <function>]` | Three concrete attacker moves against that path. Specific addresses, values, states — never abstractions. |

Rules:

1. Triggers are not optional. Condition fires → marker follows.
2. Use the literal `[Tool: ...]` syntax. Marker counts are extracted after the run
   and written into the manifest.
3. Extra markers are fine. Skipping one after its trigger fired is not.
4. The protocol is about reasoning depth, not output volume. Heavy use produces
   the audit; light use produces the surface-level scan that is the failure mode
   of every junior auditor.

Marker counts near zero are recorded as workflow violations and downgrade the
weight of your findings.

---

## Cross-contract patterns

When you find a bug in one contract, **weaponize that pattern across every other
contract in your slice.** Search by function name AND by code pattern. Finding
native/ERC20 confusion in `ContractA.onRevert` means checking every other
contract's `onRevert` — missing a repeat instance is an audit failure.

After scanning: escalate every finding to its worst exploitable variant (a DoS
may be hiding fund theft). Then revisit every function where you found something
and attack the other branches.

---

## Do not report

Admin-only functions doing admin things. Standard DeFi trade-offs (MEV, rounding
dust, first-depositor with MINIMUM_LIQUIDITY). Self-harm-only bugs. "Admin can
rug" with no concrete mechanism. Gas optimizations. Style, naming, NatSpec
completeness — unless a NatSpec claim contradicts the code, which is a real
finding.

---

## Output contract

**You write JSON Lines, not prose.**

Write to `{bundle_dir}/ledger/agent-<your_id>.jsonl`. One JSON object per line,
each conforming to `schemas/finding.schema.json`. The file is validated
mechanically; prose in it is a validation failure, and an invalid record gets one
repair attempt before it is quarantined out of the report.

### Minimum record

```json
{
  "schema_version": "3.0.0",
  "kind": "FINDING",
  "agent_id": "oracle-expert",
  "contract": "PriceFeed",
  "file": "src/PriceFeed.sol",
  "function": "latestPrice",
  "lines": [88, 96],
  "bug_class": "missing-staleness-check",
  "group_key": "PriceFeed|latestPrice|missing-staleness-check",
  "lane": "token-oracle-statefulness",
  "domain": "oracle",
  "axes": ["provenance", "accounting"],
  "severity_claim": "high",
  "root_cause": "latestPrice returns answer without comparing updatedAt to maxAge.",
  "description": "Any consumer prices collateral off a feed that may be arbitrarily stale, so a halted feed lets a borrower keep an overvalued position indefinitely.",
  "path": "borrower -> LendingPool.borrow -> PriceFeed.latestPrice -> stale answer -> over-borrow",
  "proof": {
    "kind": "numeric-trace",
    "content": "Feed halts at t0 with answer=2000e8. At t0+7d spot is 1200e8. borrow() still values 1 unit at 2000e8, permitting 66% more debt than solvency allows.",
    "values": { "stale_answer": "2000e8", "spot": "1200e8", "delta": "+66%" }
  },
  "fix": {
    "label": "validate",
    "summary": "Revert when block.timestamp - updatedAt exceeds maxAge.",
    "add_lines": ["require(block.timestamp - updatedAt <= maxAge, \"stale\")"],
    "diff": "-        (, int256 answer,,,) = feed.latestRoundData();\n+        (, int256 answer,, uint256 updatedAt,) = feed.latestRoundData();\n+        require(block.timestamp - updatedAt <= maxAge, \"stale\");"
  },
  "devils_advocate": {
    "guards": { "note": "No staleness or sequencer check anywhere on this path.", "blocks": false },
    "reentrancy": { "note": "View path, not reentrancy-relevant.", "blocks": false },
    "access": { "note": "borrow() is permissionless.", "blocks": false },
    "by_design": { "note": "NatSpec claims freshness is validated; the code does not.", "blocks": false },
    "economic": { "note": "Profitable whenever spot falls below the stale answer; no capital cost.", "blocks": false },
    "dry_run": { "note": "Reachable from borrow() with default parameters.", "blocks": false },
    "verdict": "survives"
  },
  "poc": {
    "status": "sketch",
    "framework": "foundry",
    "code": "function testStalePriceOverBorrow() public { feed.setAnswer(2000e8); vm.warp(block.timestamp + 7 days); vm.prank(alice); pool.borrow(maxDebtAt(2000e8)); assertGt(pool.debtOf(alice), pool.solventDebtAt(1200e8)); }"
  }
}
```

### The fields that matter most

- **`group_key`** — exactly `"<contract>|<function>|<bug_class>"`. This is the
  dedup primary key; the validator recomputes it and rejects mismatches.
- **`proof`** — a FINDING without proof is a LEAD wearing a costume. Concrete
  values, traces, or state sequences from the actual code. No proof → `kind`
  becomes `LEAD`, no exceptions.
- **`axes`** — which of the six risk axes you covered. An agent that never sets
  axes cannot close coverage, and its clean functions turn into coverage gaps.
- **`fix.add_lines`** — the added lines alone, normalized. The reducer hashes
  these to decide whether two fixes are distinct, so a distinct fix survives into
  the report as its own option instead of being paraphrased away.
- **`devils_advocate`** — all six dimensions, every time. `blocks: true` with
  `verdict: "survives"` requires a named `bypass`.

### One vulnerability per record

Same root cause → one record. Different fix needed → separate record. Two
coexisting bugs in one function are two records with different `bug_class`; the
reducer keeps both, and function isolation guarantees neither is merged into a
neighbouring function.

### COVERAGE_NOTE

For every hot function in your slice that you examined and found clean, emit:

```json
{ "schema_version": "3.0.0", "kind": "COVERAGE_NOTE", "agent_id": "oracle-expert",
  "contract": "PriceFeed", "function": "decimals", "bug_class": "examined-clean",
  "group_key": "PriceFeed|decimals|examined-clean", "lane": "token-oracle-statefulness",
  "axes": ["provenance"], "severity_claim": "informational",
  "description": "Examined for provenance; returns an immutable constant with no external input.",
  "path": "n/a — coverage note",
  "fix": { "label": "validate", "summary": "None needed." },
  "devils_advocate": { "...all six...": "...", "verdict": "survives" } }
```

This is how the coverage gate tells "checked, clean" from "never looked". Both
look identical in a findings list, and only one of them is reassuring.

### Fields you must not write

`verification`, `judgment`, `corroboration`. The orchestrator owns those. Records
that set them are rejected.
