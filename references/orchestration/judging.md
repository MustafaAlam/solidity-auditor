# JUDGE — the four gates (v3)

Carried forward from v2.7 with the same four gates and the same admin-amplifier
rule. What changed: gates now run against normalized JSON records after
verification, verdicts are written into `judgment.*` as data rather than prose,
and final severity plus confidence are computed rather than chosen.

Every reduced candidate passes through the gates **in fixed order**. One verdict
per gate. No reordering, no skipping, no revisiting after a verdict is written.

`UNCERTAIN = ALLOWS`. Commit and move on.

You are not defending the code. The gates check that the claimed exploit fires
end to end — anything that interrupts the attack between the call and the harm
means the claim does not execute.

---

## Gate 1 — Reachability

Is there a concrete, unguarded call path from an external user, or from a role an
attacker can obtain, to the defective line?

- **BLOCKS** — only an owner/admin/privileged role reaches it AND that role
  cannot be obtained (no broken init, no grant path, no confused deputy).
  Check `system_map.trust_boundaries[].acquirable` before writing BLOCKS.
- **ALLOWS** — any external or permissionless path, or a path needing only a role
  that is easy to acquire or that the attacker already holds via another finding.
- **IRRELEVANT** — dead code, never called.
- **UNCERTAIN** — treat as ALLOWS.

## Gate 2 — Impact

Does the path produce fund loss, permanent DoS of a core user flow, selective
censorship, or a break of a core economic invariant (solvency, conservation of
value, fee fairness)?

- **BLOCKS** — pure self-harm, pure gas grief with no lasting state damage, pure
  admin convenience, or "theoretical under conditions that never occur".
- **ALLOWS** — real economic or availability impact on honest users, LPs, or the
  protocol.
- **IRRELEVANT** — the behaviour is the intended design (MEV, first-depositor
  dust after MINIMUM_LIQUIDITY).
- **UNCERTAIN** — treat as ALLOWS.

## Gate 3 — Code-level defect

Is the root cause a defect visible in the source — missing check, wrong rounding,
missing mirror update, late nonce write — rather than an assumption about
off-chain behaviour or future governance?

- **BLOCKS** — the finding rests on "admin would never do X" or "the oracle is
  honest" with no on-chain enforcement.
- **ALLOWS** — the defect is in the code.
- **IRRELEVANT** — a documented, accepted design trade-off.
- **UNCERTAIN** — treat as ALLOWS.

## Gate 4 — Fixability & minimality

Is there a small, local change that removes the defect without breaking the
intended happy path?

- **BLOCKS** — the only fix is a protocol redesign. (`fix.label == "redesign"` is
  a strong signal, not an automatic BLOCKS — check whether a local fix exists.)
- **ALLOWS** — a require, a reordering, a Ceil instead of a Floor, moving a hook
  before a callback, a one- or two-line change.
- **IRRELEVANT** — already mitigated elsewhere and the agent missed it.
- **UNCERTAIN** — treat as ALLOWS.

---

## Admin-action findings

Applies **only** when the *harm step* is an admin action, not when an
unprivileged attacker is the actor.

If harm requires the admin acting maliciously or against documented intent,
**REJECT** — do not even emit as a LEAD — unless the record names a concrete
unprivileged amplifier in `judgment.admin_amplifier`:

- **race** — admin sets X mid-flow; an unprivileged user exploits the window
  before the update propagates.
- **retroactive-sweep** — an admin update rewrites a pending value already
  credited.
- **asymmetric-formula** — admin output chains into a formula an unprivileged
  actor profits from.
- **access-gap** — missing guard, tautological auth, or missing init guard; the
  access mechanism itself is the bug.

`none-named` → REJECTED. An amplifier named → judge that unprivileged path
through Gates 1–4 as usual.

---

## Safe patterns — do not flag

- `unchecked` in 0.8+ (verify the overflow reasoning, then move on)
- Explicit narrowing casts in 0.8+ (they revert on overflow)
- MINIMUM_LIQUIDITY burn on first deposit
- SafeERC20 (`safeTransfer` / `safeTransferFrom`)
- `nonReentrant` — only flag cross-contract or hook paths the mutex does not cover
- Two-step admin transfer
- Consistent protocol-favouring rounding, unless it compounds or rounds to zero
- **New in v3:** `transient` / `tstore` used as a same-transaction reentrancy
  lock that is cleared on every exit path — flag only if a path leaves it set
- **New in v3:** ERC-7201 namespaced storage in an upgradeable or delegated
  contract — that is the correct pattern, not a smell

---

## Verifier interaction

The verifier verdict is an input to judging, not a replacement for it.

| `verification.verifier_verdict` | Effect on judging |
| --- | --- |
| `REFUTED` | `final_severity = rejected` immediately; skip the gates. Move any `residual_risk` into a new LEAD record. |
| `UNREACHABLE-CODE` | Gate 1 is forced to BLOCKS. |
| `WEAKENED` | run all four gates normally; the confidence penalty already applied. |
| `CONFIRMED` | run all four gates normally. A CONFIRMED finding can still be rejected — the verifier checks whether the claim is *true*, the gates check whether it *matters*. |
| `NOT-RUN` | run all four gates normally; the report says it was not verified. |

---

## Lead promotion

A LEAD becomes a FINDING only when **both** hold:

1. computed `confidence >= 70` (see `confidence.md`), and
2. the full exploit chain is present in the source **or** `agent_count >= 2`
   from independent agents.

Additional rules, unchanged from v2.7:

- Multi-agent agreement never overrides a Gate 1 or Gate 2 BLOCKS. If the path is
  interrupted before harm, it stays a LEAD.
- A root cause confirmed as a FINDING in one contract promotes the identical
  pattern in every other in-scope contract where it appears — check for it
  explicitly rather than waiting for an agent to have found it.
- Never reason from deployer intent. Only from what the code allows.
- For medium and above, a promoted LEAD still needs `poc.status != absent`. A
  medium with no PoC and no stated reason stays a LEAD.

---

## Final severity

Severity is assigned from impact and reachability, then bounded by confidence —
so an unverified claim cannot present itself as a certainty.

| Impact (Gate 2) × Reachability (Gate 1) | Severity |
| --- | --- |
| fund loss or permanent core DoS, permissionless path | **critical** |
| fund loss or permanent DoS, path needs an acquirable role or a precondition | **high** |
| griefing that freezes trading, selective censorship, large economic leakage | **high** |
| state inconsistency, non-atomic admin, gameable asymmetric branches | **medium** |
| reachable but bounded, or requires an unlikely-but-possible precondition | **low** |
| standards conformance, docs mismatch, defensive hardening | **informational** |

Then clamp: **`confidence < 60` caps severity at `medium`; `confidence < 40` caps
at `low`.** A critical this system is not confident about is a medium that needs
more work, and saying so is more useful than saying "critical".

Write the outcome into `judgment.final_severity` and one sentence into
`judgment.rationale`. Anything with BLOCKS on Gate 1, 2, or 3, or a failed
admin-amplifier check, gets `rejected` and appears only in the rejected section.
