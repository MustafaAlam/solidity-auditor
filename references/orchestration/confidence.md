# Confidence — computed, never chosen

v2.7 wrote `conf 90` on strong findings and `conf 75` on promoted leads. Those
numbers were conventions, not measurements: nothing produced them and nothing
could disagree with them.

v3 computes confidence from five observable inputs. The formula lives in
`scripts/reduce.py::compute_confidence` and is the single source of truth — this
document explains it, it does not duplicate it.

---

## The formula

```
confidence = base(proof)
           + corroboration_bonus
           + poc_bonus
           + verifier_delta
           - unbypassed_da_blockers
           clamped to [0, 99]
```

### base — how strong is the evidence

| `proof.kind` | base |
| --- | --- |
| `counterexample` | 60 |
| `numeric-trace` | 60 |
| `state-sequence` | 55 |
| `quoted-code` | 45 |
| (kind = LEAD, no proof) | 30 |

Quoted code scores lowest on purpose. "Here is the line" shows the defect exists;
it does not show the defect is reachable or harmful.

### corroboration — independent agreement

`+5` per additional independent agent, capped at `+15`.

Capped because agreement saturates fast: three agents finding the same reentrancy
is meaningfully stronger than one, six is not meaningfully stronger than three.
Agents share a base model and a source bundle, so their errors correlate — treat
agreement as a weak signal, not a proof.

### poc — did it execute

| `poc.status` | bonus |
| --- | --- |
| `passing` | +25 |
| `compiled` | +15 |
| `sketch` | +5 |
| `not-feasible` | 0 |

The gap between `sketch` (+5) and `passing` (+25) is the largest single term in
the formula. That is deliberate: a test that runs and fails the assertion is the
only evidence here that is not an argument.

### verifier — did it survive attack

| `verification.verifier_verdict` | delta |
| --- | --- |
| `CONFIRMED` | +15 |
| `NOT-RUN` | −5 |
| `WEAKENED` | −15 |
| `UNREACHABLE-CODE` | −40 |
| `REFUTED` | −100 (floors to 0; the record is rejected at judging) |

`NOT-RUN` costs points. Unchecked is not the same as fine, and the arithmetic
should say so.

### Devil's Advocate blockers

`−10` for each of the six dimensions where `blocks: true` and no `bypass` is
named. Up to −60.

The schema already rejects a record claiming `verdict: "survives"` with an
unbypassed blocker, so this term mostly catches honest self-demotion — an agent
saying "the guard mostly holds but here is why it still fires" keeps its points
by naming the bypass, and loses them by hand-waving.

---

## Reading the output

| Range | Meaning | Report treatment |
| --- | --- | --- |
| 85–99 | executed PoC or corroborated + verifier-confirmed | headline finding |
| 70–84 | solid evidence, survived verification | finding |
| 60–69 | real defect, some doubt about reach or impact | finding, severity capped at medium |
| 40–59 | plausible trail, unproven | lead, severity capped at low |
| 0–39 | weak, refuted, or unreachable | lead or rejected |

The caps are enforced in `judging.md`. They exist so that severity and confidence
cannot contradict each other in the summary table — a reader scanning severities
should never find a "critical" that the system privately doubts.

---

## Calibration

The formula's constants are a starting point, not physics. They are tuned against
the eval corpus:

1. Run `evals/score.py` with `--calibration`.
2. It bins findings by predicted confidence and reports the observed true-positive
   rate per bin.
3. A well-calibrated system has the 70–84 bin land near 70–84% true positives.
4. If a bin is systematically off, adjust the constants in `reduce.py`, re-run,
   and record the change in `CHANGELOG.md` with the before/after numbers.

**Do not adjust confidence constants without an eval run.** That is the whole
reason they are constants in a file rather than judgment in a prompt — a number
that moves for unmeasured reasons is worse than no number, because it looks
like a measurement.
