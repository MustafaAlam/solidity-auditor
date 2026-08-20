# VERIFY — the adversarial verifier loop

This phase is the single largest precision change in v3, and the one that most
directly targets false positives.

## Why a separate phase

v2.7 asked each hunting agent to run its own Devil's Advocate before emitting. It
is a good instruction and it is not enough. An agent that has just spent its
context building a case for a bug is the worst available judge of that bug —
it is scoring its own homework, with every sunk-cost pressure pointing at
"survives".

The decoupled-audit literature makes the same point empirically: separating
generation from verification, and verifying against *independent* candidates
rather than self-correcting a single draft, is what limits hallucination
propagation. ADK calls this the Generator-and-Critic pattern; wrap it in a
LoopAgent with an exit condition and you have iterative refinement.

So v3 keeps the in-agent DA (it improves the *input*) and adds a structurally
separate critic whose incentives point the other way.

## The asymmetry that makes it work

The verifier is not asked "is this finding correct?". It is asked to **kill the
finding**. It succeeds by refuting. A finding that survives an agent trying to
destroy it is worth more than a finding blessed by an agent trying to defend it.

The verifier also receives deliberately narrow context:

- the finding record (JSON), and
- only the code slice it names — the function, its callees, its guards, its
  callers.

It does **not** receive the hunting agent's reasoning, its markers, or its
enthusiasm. Fresh eyes on the code and the claim. That isolation is the point;
leaking the original narrative re-imports the bias the phase exists to remove.

---

## Loop shape

```
for each candidate where severity_claim >= medium (>= low at budget deep+):
    iteration = 0
    while iteration < max_iterations:
        verdict = adversarial_verifier(candidate, code_slice)
        if verdict in {CONFIRMED, REFUTED, UNREACHABLE-CODE}:
            break                      # escalate=True — exit the loop
        if verdict == WEAKENED:
            candidate = repair(candidate, verdict.refutation)   # one repair attempt
            iteration += 1
    write verdict into candidate.verification
```

`max_iterations` comes from the budget tier (1 / 2 / 2 / 3). The loop MUST have a
hard ceiling — an unbounded critic loop is how a multi-agent system burns a
budget arguing with itself.

**Exit conditions, explicitly:**

| Verdict | Meaning | Effect |
| --- | --- | --- |
| `CONFIRMED` | verifier tried and failed to refute | +15 confidence, exit |
| `WEAKENED` | the claim is partly true, the headline overstates it | −15 confidence, one repair pass, then exit |
| `REFUTED` | a concrete code path defeats the claim | rejected; `residual_risk` may survive as a LEAD |
| `UNREACHABLE-CODE` | the defect is real but nothing can reach it | −40 confidence, usually informational |
| `NOT-RUN` | below threshold or budget exhausted | −5 confidence, stated in the report |

`NOT-RUN` is a real value and must be recorded, not silently omitted. A reader
needs to distinguish "survived a verifier" from "was never checked".

---

## Repair, not re-litigation

When a verdict is `WEAKENED` the repair pass may only:

- narrow the claimed severity,
- narrow the claimed path to the part that survives,
- move an unsupported element into `residual_risk`.

It may **not** invent a new attack path to save the finding. A finding that needs
a different attack path is a different finding, and it goes back through HUNT on
the next run rather than being rescued here. Without that rule the loop becomes a
machine for manufacturing justifications.

---

## PoC escalation

The verifier's strongest refutation tool is execution. When
`system_map.scope.compiles` is true and a Foundry project exists:

1. `poc-writer` turns the candidate's sketch into a real test file under a
   scratch directory — never inside the user's `test/`.
2. Run it: `forge test --match-path <scratch>/<Name>.t.sol -vvv`.
3. Record the outcome in `poc.status`:
   - test compiles and the exploit assertion passes → `passing` (+25 confidence)
   - test compiles but the assertion fails → strong evidence for `REFUTED`
   - test does not compile after one repair attempt → stays `sketch`, note why

A passing PoC is the only evidence that fully retires the hallucination question.
Everything else is argument.

**Never** run a PoC that sends real transactions, touches a fork endpoint with
credentials, or writes outside the scratch directory. Local EVM only.

---

## What the verifier must always record

Even for `CONFIRMED`, the verifier writes its **strongest counter-argument** into
`verification.refutation`. This is non-negotiable and it is rendered into the
report.

A reader who sees the best case against a finding can calibrate their own trust.
A reader who sees only the case for it cannot. This single field does more for
the report's credibility than the verdict does.

---

## Cost

Verification roughly doubles the token cost of the candidates it examines — but
it examines only medium-and-above candidates, which are a small fraction of raw
records. On a typical run it adds 10–20% to total spend.

That is the trade v3 makes deliberately: precision is what an audit is *for*, and
a report with three real findings beats one with three real findings buried in
eleven plausible-sounding ones.
