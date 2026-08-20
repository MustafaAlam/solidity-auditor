# Eval harness

v2.7's changelog contains four versions of unmeasured improvement. Every entry
describes a change; none shows that the change helped. This directory exists so
v3 does not repeat that.

**Rule: no version bump without a before/after on recall, precision, and context
amplification.**

---

## Layout

```
evals/
  corpus/manifest.json      # the cases: id, source, what it exercises
  ground-truth/<case>.json  # the known bugs in each case
  results/<case>.json       # a run's reduced.json, copied here
  score.py                  # scoring, calibration, baseline diff
  baselines/<version>.json  # frozen scorecards
```

## Ground-truth format

```json
{
  "case_id": "c4-2024-vault-inflation",
  "source": "https://code4rena.com/reports/...",
  "contracts": ["src/Vault.sol"],
  "notes": "Public contest report; findings below are the judged H/M set.",
  "bugs": [
    {
      "contract": "Vault",
      "function": "deposit",
      "bug_class": "share-inflation",
      "severity": "high",
      "description": "First depositor donates to inflate share price and steals rounding from later depositors.",
      "source_ref": "H-01"
    }
  ]
}
```

`severity` uses the judged severity from the original report, not the reporter's
claim. Where a report and a sponsor disagreed, record the judge's call and note
the dispute — those cases are the interesting ones for calibration.

---

## Building the corpus

Start at **20 cases**. Anthropic's multi-agent work found that with effect sizes
this large — moving a success rate from 30% to 80% — a few dozen cases is enough
to see a change, and waiting for a perfect corpus means never measuring anything.

Spread them across four groups:

| Group | Count | Purpose |
| --- | --- | --- |
| **Known-vulnerable** | 8 | public contest findings with judged severity. Measures recall. |
| **Patched pairs** | 4 | the same contract before and after its fix. The strongest signal available: a system that flags both is pattern-matching, not reasoning. |
| **Clean** | 4 | audited, unexploited production code. Measures false-positive rate — the metric that decides whether anyone trusts the tool. |
| **Surface coverage** | 4 | one each for EIP-7702 delegation, proxy storage collision, transient-storage lock, cross-chain replay. Confirms the new v3 lenses fire at all. |

Sources: Code4rena and Sherlock public reports, the Solodit archive, SC-standard
vulnerable-contract sets, and the Damn Vulnerable DeFi / Ethernaut families for
the surface-coverage group.

**Keep the corpus out of any prompt.** The moment a case appears in a bundle or a
specialty file, its score stops meaning anything.

---

## Running

```bash
# one case
/audit --budget standard evals/corpus/cases/vault-inflation/src
cp .audit-*/reduced.json evals/results/c4-2024-vault-inflation.json

# score the set
python3 score.py --runs results/ --ground-truth ground-truth/ \
                 --out scorecard.md --json-out scorecard.json --calibration

# compare against the frozen baseline
python3 score.py --runs results/ --ground-truth ground-truth/ \
                 --baseline baselines/v3.1.0.json --out scorecard.md
```

## What the metrics mean

| Metric | Reading it |
| --- | --- |
| **Recall** | did the lenses fire? A miss means a specialty did not look, or routing did not spawn it. Check `roster.json` first — a bug missed because its agent never ran is a routing bug, not a hunting bug. |
| **Precision** | did the verifier and gates hold? Every false positive got through an adversarial verifier and four gates, so each one is a specific defect in the pipeline, not general noise. |
| **FP per case** | the number that decides adoption. A tool with 90% recall and eleven false positives per contract does not get used twice. |
| **Near misses** | right function, wrong bug class. Usually a taxonomy problem — add a synonym rather than chasing the model. |
| **Lead hits** | leads that pointed at real bugs. High lead-hit with low recall means the promotion threshold in `judging.md` is too strict. |
| **Calibration** | does confidence 70–84 land near 70–84% true positives? Drift means the constants in `reduce.py::compute_confidence` need retuning. |

## Reading a regression

Recall drops → check routing before blaming the agents. The most common cause of
a v3 recall regression is an evidence flag that stopped firing in MAP, which
silently removes a specialist from the roster. `roster.json` names every agent
that ran and why; a missing `spawn_reason` is the whole answer.

Precision drops → read the false-positive list before touching any constant. Two
or three FPs sharing a bug class usually means one specialty file grew an
over-eager instruction.

## Manual review still matters

Automated scoring catches what it has labels for. Read three full reports by hand
per release. Look for the things the score cannot see: findings that are
technically true and useless, summary prose that overstates, PoCs that would not
actually compile, coverage-gap sections so long nobody reads them.

Anthropic's guidance on this is blunt and correct — human testing catches the
edge cases and biases programmatic checks overlook. The score tells you whether
something moved. Reading the report tells you whether it got better.

## Freezing a baseline

On release: run the full corpus, copy `scorecard.json` to
`baselines/v<version>.json`, and paste the aggregate table into `CHANGELOG.md`.

That table is the version's actual claim. Everything else in a changelog is a
description of intent.
