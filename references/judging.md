# Judging Gates (Turn 4)

Every deduped candidate (FINDING or LEAD) must pass through these four gates in fixed order. One-line verdict per gate. No reordering, no skipping, no revisiting after a verdict is written.

`UNCERTAIN = ALLOWS` for the purpose of this audit. Commit and move on.

You are not defending the code. The gates verify the claimed exploit fires end-to-end — anything that interrupts the attack between the call and the harm means the claim does not execute.

## Gate 1 — Reachability

Is there a concrete, unguarded call path from an external user (or a role that an attacker can obtain) to the defective line?

- BLOCKS: only owner/admin/privileged role can reach it AND that role cannot be obtained by an attacker (no broken init, no role grant path, no confused deputy).
- ALLOWS: any external or permissionless path, or a path that only requires a role that is easy to acquire or that the attacker already holds via another bug.
- IRRELEVANT: dead code / never called.
- UNCERTAIN: treat as ALLOWS.

## Gate 2 — Impact

Does the path produce one of: fund loss, permanent DoS of a core user flow (swap/add/remove), selective censorship of users, or a break of a core economic invariant (solvency, conservation of value, fee fairness)?

- BLOCKS: pure self-harm, pure gas grief with no lasting state damage, pure admin convenience, or "theoretical under perfect conditions that never occur".
- ALLOWS: real economic or availability impact on honest users / LPs / protocol.
- IRRELEVANT: the "bug" is actually the intended design (e.g. MEV, first-depositor dust after MINIMUM_LIQUIDITY).
- UNCERTAIN: treat as ALLOWS.

## Gate 3 — Code-level defect

Is the root cause a real defect in the source (missing check, wrong rounding, missing mirror update, late nonce write, etc.) rather than an assumption about off-chain behavior or future governance?

- BLOCKS: the finding relies on "admin would never do X" or "the oracle is honest" without an on-chain enforcement.
- ALLOWS: the defect is visible in the code itself.
- IRRELEVANT: the finding is a design trade-off that is documented and accepted.
- UNCERTAIN: treat as ALLOWS.

## Gate 4 — Fixability & minimality

Is there a small, local change that eliminates the defect without breaking the intended happy path?

- BLOCKS: the only "fix" is a complete redesign of the protocol.
- ALLOWS: a one- or two-line change, a require, a reordering, a Ceil instead of Floor, moving a hook before the callback, etc.
- IRRELEVANT: already fixed or mitigated by another mechanism that the agent missed.
- UNCERTAIN: treat as ALLOWS.

## Admin-action findings

Applies ONLY when the *harm step* is an admin/owner action, not when an unprivileged attacker is the actor.

If harm requires the admin acting maliciously or against documented intent, **REJECT** (do not emit as a LEAD) unless the body names a concrete unprivileged amplifier:

- **race** — admin sets X mid-flow; an unprivileged user exploits the window before the update propagates.
- **retroactive sweep** — an admin update rewrites a pending value already credited.
- **asymmetric formula** — admin output chains into a formula an unprivileged actor profits from.
- **access gap** — missing guard, tautological auth, or missing init guard (the access mechanism itself is the bug).

No amplifier named → REJECTED. Amplifier named → judge that unprivileged path through Gates 1–4 as usual.

## Safe patterns (do not flag)

- `unchecked` in 0.8+ (verify the overflow reasoning is correct)
- Explicit narrowing casts in 0.8+ (revert on overflow)
- MINIMUM_LIQUIDITY burn on first deposit
- SafeERC20 (`safeTransfer` / `safeTransferFrom`)
- `nonReentrant` (only flag cross-contract / hook paths the mutex does not cover)
- Two-step admin transfer
- Consistent protocol-favoring rounding unless compounding or zero-rounding

## Lead promotion rules (after the four gates)

- A LEAD becomes a FINDING (confidence 75) only if:
  - the full exploit chain is present in the source, OR
  - ≥2 agents flagged the same issue (even if each was only a LEAD).
- Multi-agent agreement does **not** override a Gate 1 or Gate 2 BLOCKS. If the code path is interrupted before harm, demote back to LEAD.
- Same root cause confirmed as FINDING in one contract → promote the identical pattern in every other in-scope contract where it appears.
- Never reason from deployer intent. Only from what the code allows.

## Final filter

Exclude anything that received BLOCKS on Gate 1, 2 or 3, or that failed the admin-amplifier rule. Keep everything that received ALLOWS (or UNCERTAIN) on all four gates. Promoted LEADs are written as FINDINGs with conf 75.
