# solidity-auditor v3.0.0

A production multi-agent system for Solidity security auditing. Ships as a
Claude skill and as a deployable service running the same graph.

```
PREFLIGHT → INGEST → MAP → [human gate] → ROUTE → HUNT → VERIFY
          → REDUCE → JUDGE → REPORT → POSTFLIGHT
```

## What is here

```
SKILL.md                  the orchestrator — start here
CHANGELOG.md              what changed from v2.7 and why
MIGRATION.md              dropping your existing agent files in
VERSION

references/
  orchestration/          routing, verification, reliability, judging,
                          confidence, observability, report format, prompts
  schemas/                finding, system map, run manifest (JSON Schema)
  scripts/                validate_findings.py, reduce.py, render_report.py
  hacking-agents/         the SOP, shared rules, and every specialty lens
  evals/                  corpus manifest, ground truth format, score.py

service/                  the deployable twin — FastAPI, A2A, Cloud Run, ADK
```

## Quick start — as a skill

Install the bundle as a skill, copy your v2.7 specialty files in per
`MIGRATION.md`, then:

```
audit
audit --budget quick src/Vault.sol
audit --diff HEAD~1 --yes            # CI mode
```

## Quick start — as a service

```bash
cd service
pip install -e '.[server,anthropic,dev]'
pytest -q                                          # 44 tests, offline
python -m audit_mas.cli roster ./contracts         # see the routing decision
python -m audit_mas.cli audit  ./contracts --budget standard
```

## The five things that make it production

**Routing.** Agents spawn because the map found evidence for them. Six always-on
core lanes, then specialists triggered by what the code actually contains. Cost
tracks attack surface instead of tracking a constant.

**A schema.** Agents emit validated JSON. A malformed record is quarantined and
counted, never silently dropped — because "nobody found anything" and "the
output was unparseable" must not look alike.

**An adversarial verifier.** A separate critic with narrow context tries to kill
every medium-and-above finding. It records its strongest counter-argument even
when the finding survives, and that argument is printed in the report.

**Deterministic reduction.** Dedup, function isolation, fix preservation,
completeness and coverage are code. Confidence is a formula. Severity is clamped
by confidence.

**Visible failure.** Per-agent status, one retry, quorum, DEGRADED banners, a
run manifest with cost and coverage, and a hard refusal to render a report from
a half-dead fan-out.

## Before changing anything

Run the eval corpus. `references/evals/README.md`.

No version bump without a before/after on recall, precision and context
amplification — that rule is the reason this version exists.

## Provenance

Hunting lenses adapted from [evm-cortex](https://github.com/ccashwell/evm-cortex)
and the Pashov-style attacker methodology carried forward from v2.7.

Architecture informed by
[Anthropic's multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
(orchestrator-worker, token economics, evaluation, production reliability),
Google's [production MAS codelab](https://codelabs.developers.google.com/codelabs/production-ready-ai-roadshow/1-building-a-multi-agent-system/building-a-multi-agent-system)
and [multi-agent guide](https://cloud.google.com/discover/what-is-a-multi-agent-system)
(loop/sequential/parallel composition, structured output, A2A, agent cards), and
the decoupled-audit literature on separating generation from verification
([arXiv:2606.03128](https://arxiv.org/html/2606.03128v1)).

2026 threat surface from
[Dedaub's Solidity vulnerability guide](https://dedaub.com/blog/solidity-security-vulnerabilities/)
and [Zealynx's EIP-7702 wallet security research](https://www.zealynx.io/research/smart-contracts/eip-7702-wallet-security).
