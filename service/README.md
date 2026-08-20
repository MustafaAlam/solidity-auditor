# audit-mas — the deployable twin

Same phase graph as the Claude skill in `../SKILL.md`, same finding schema, same
reduction arithmetic — running as a service instead of inside a conversation.

Use the skill when you are auditing while you develop. Use this when you need the
audit to run in CI, on a schedule, behind an API, or across agents that scale
independently.

The two halves are kept honest by `tests/test_parity.py`, which asserts that the
service's confidence formula, synonym clusters, fix fingerprints and grouping
produce the same output as `references/scripts/reduce.py`. When they drift, a
scorecard from one stops being comparable with the other — so the drift is a
test failure, not a footnote.

---

## Install

```bash
cd service
pip install -e '.[server,anthropic,dev]'
cp .env.example .env      # then fill in one provider
```

Python 3.11+. The core depends on pydantic and httpx only; FastAPI, the vendor
SDKs and ADK are all optional extras.

## Try it without a key

Every test runs against `StubProvider` — no network, no cost:

```bash
pytest -q                 # 44 tests, well under a second
```

The routing decision is worth looking at before anything else, because it is what
makes this cheaper than a fixed fan-out:

```bash
python -m audit_mas.cli roster ./path/to/contracts --budget standard
```

```
Budget: standard   slicing: True   SLOC: 4183

agent                     role      tier    slice   reason
----------------------------------------------------------------------------
access-control            lane      deep    full    core lane: permission model
boundary                  lane      deep    full    core lane: edges, zeros, maxima
...
oracle-expert             domain    deep    domain  evidence.has_oracle
proxy-upgrade             platform  deep    domain  evidence.has_proxy_or_upgrade
fuzzer                    SKIPPED                   budget=standard, requires deep
```

## Run an audit

```bash
python -m audit_mas.cli map    ./contracts                    # SystemMapArtifact
python -m audit_mas.cli audit  ./contracts --budget standard  # full graph
python -m audit_mas.cli audit  ./contracts --budget deep --yes --workdir .run
```

`--yes` skips the map gate. Required for unattended runs and deliberately not
the default: a wrong map sends every downstream agent to the wrong place, and a
human who knows the protocol spots that in fifteen seconds.

Artifacts land in `--workdir`:

| File | Contents |
| --- | --- |
| `run.json` | manifest — per-agent status, timing, quorum, coverage, cost |
| `roster.json` | which agents ran and why |
| `ledger/agent-*.jsonl` | raw validated findings, one file per agent |
| `ledger/quarantine.jsonl` | records that failed schema, with errors |
| `verified.json` | findings after the adversarial verifier |
| `reduced.json` | deduplicated, judged, scored |

Render a report from `reduced.json` with the shared script:

```bash
python3 ../references/scripts/render_report.py \
  --reduced .run/reduced.json --manifest .run/run.json --out report.md
```

---

## Architecture

```
ingest.py         static scope + evidence extraction (regex, runs before any model call)
core/router.py    evidence -> roster, tiers, slices          [Coordinator/Dispatcher]
agents/base.py    hunt worker, provider-agnostic             [Parallel fan-out/gather]
agents/verifier.py adversarial critic with bounded loop      [Generator/Critic]
core/ledger.py    append-only per-agent JSONL blackboard
core/reduction.py dedup, fix preservation, confidence, coverage
orchestrator.py   the phase graph, quorum, manifest
a2a_server.py     one agent as an HTTP microservice          [A2A]
a2a_client.py     remote agents behind the local interface
adk_graph.py      optional Google ADK composition
```

Three decisions worth knowing about:

**Evidence extraction is regex, not a model call.** It runs before anything
costs money and it decides the roster, so a false negative loses a whole
specialty. Every heuristic errs toward firing: a spurious specialist costs one
agent's tokens, a missing one costs a bug class. The MAP phase refines it; it
does not replace it.

**The ledger is append-only, one file per agent.** That is what makes a 25-way
concurrent fan-out safe with no locking, and what makes a crashed run
recoverable. It is also the boundary where untrusted agent output becomes
trusted pipeline input — nothing gets past `append()` without passing the schema,
and rejected records go to quarantine rather than to `/dev/null`.

**Quorum gates the report.** Below 50% valid returns, or with any core lane
missing, the run refuses to produce a findings list at all. Absences in a
half-dead fan-out read as clean bills of health, which is worse than no report.

---

## A2A mode

Each agent runs as its own service with an agent card at
`/.well-known/agent-card.json`. Locally:

```bash
./run_local.sh          # six agents on :8001-:8006, prints the cards
```

The orchestrator finds them through `AGENT_CARD_URL_<AGENT_ID>` env vars, and
`RemoteHuntAgent` matches `HuntAgent`'s interface exactly — swapping transports
is a constructor change, not a rewrite.

### Cloud Run

One image, many services. The agent's identity comes from `AGENT_ID` at runtime:

```bash
gcloud builds submit --tag gcr.io/$PROJECT/audit-mas ..

for spec in access-control:lane oracle-expert:domain proxy-upgrade:platform \
            account-abstraction:platform adversarial-verifier:verifier; do
  id="${spec%%:*}"; role="${spec##*:}"
  gcloud run deploy "audit-${id}" \
    --image "gcr.io/$PROJECT/audit-mas" \
    --set-env-vars "AGENT_ID=${id},AGENT_ROLE=${role},MODEL_TIER=deep" \
    --set-secrets "ANTHROPIC_API_KEY=anthropic-key:latest" \
    --region us-central1 --no-allow-unauthenticated --memory 1Gi --timeout 900
done
```

Then capture each deployed URL into the orchestrator's environment before
starting it — the codelab's point about verifying service URLs before
configuring downstream agents applies exactly here.

Notes for production:

- `--no-allow-unauthenticated` and service-to-service IAM. An open audit worker
  is an open LLM proxy.
- `--timeout 900` at minimum; a deep-tier agent legitimately runs for minutes.
- Concurrency 1–2 per instance. These are long, token-heavy requests, not web
  traffic.
- Rainbow deploys rather than a hard cutover: agents are long-running and
  stateful within a run, so shifting traffic gradually avoids killing audits
  mid-flight.

---

## Providers

| Class | Selected by |
| --- | --- |
| `AnthropicProvider` | `ANTHROPIC_API_KEY` |
| `VertexProvider` | `GOOGLE_GENAI_USE_VERTEXAI=true` |
| `OpenAICompatibleProvider` | `OPENAI_BASE_URL` or `OPENAI_API_KEY` |
| `StubProvider` | nothing configured — tests, CI, dry runs |

`providers.from_env()` picks one. Tier→model mapping lives in
`agents/base.py::DEFAULT_MODELS`; override it per deployment.

The `OpenAICompatibleProvider` exists for the distilled-small-model path: point
triage lanes at a small local model and reserve a large one for deep lanes and
verification. The decoupled-audit literature reports specialized small models
beating much larger general ones on the classification half of this work, and the
tier system is already the right shape to exploit that.

## ADK

`adk_graph.py` composes the same graph from ADK primitives —
`ParallelAgent` for HUNT, `LoopAgent` for VERIFY, `SequentialAgent` for the
whole. It is optional (`pip install '.[adk]'`) and the framework-free
orchestrator remains the supported path.

One detail that matters if you extend it: parallel ADK agents share session
state, so every sub-agent needs a distinct `output_key`. A shared key is a race
that silently loses one agent's findings — the worst possible failure mode for an
audit, because it looks like a clean result.
