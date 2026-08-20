"""The phase graph — parallel fan-out/gather, then generator/critic, then reduce.

This is the same graph the skill runs, expressed as code:

    INGEST -> MAP -> [gate] -> ROUTE -> HUNT -> VERIFY -> REDUCE -> JUDGE -> REPORT

Two properties are worth stating because they are easy to lose:

* **Every failure is visible.** An agent that times out, returns prose, or dies
  is recorded in the manifest with a status. A degraded run that says so is
  useful; one that looks complete is dangerous.
* **Quorum gates the report.** Below 50% valid returns, or with any core lane
  missing, no findings report is produced at all — because absences in a
  half-dead fan-out read as clean bills of health.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import time
import uuid

from .agents.base import AgentResult, HuntAgent, LLMProvider
from .agents.verifier import AdversarialVerifier
from .core.ledger import Ledger
from .core.reduction import axis_coverage, reduce_findings, severity_cap
from .core.router import CORE, build_roster, verify_iterations
from .schemas import AgentRun, Quorum, Roster, RunManifest, SystemMap

CORE_IDS = {a for a, _ in CORE}
QUORUM_THRESHOLD = 0.8
ABORT_THRESHOLD = 0.5

TIMEOUTS = {"quick": 180.0, "standard": 480.0, "deep": 900.0, "exhaustive": 1800.0}


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class Orchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        workdir: pathlib.Path,
        *,
        budget: str = "standard",
        run_id: str | None = None,
        max_concurrency: int = 10,
    ):
        self.provider = provider
        self.budget = budget
        self.workdir = pathlib.Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or f"audit-{uuid.uuid4().hex[:8]}"
        self.ledger = Ledger(self.workdir / "ledger")
        self.max_concurrency = max_concurrency
        self.manifest = RunManifest(
            run_id=self.run_id,
            started_at=_now(),
            config={"budget": budget, "mode": "repo"},
        )

    # -- manifest -------------------------------------------------------
    def _phase(self, name: str, status: str, started: float | None = None, note: str = "") -> None:
        entry = {"name": name, "status": status, "started_at": _now()}
        if started is not None:
            entry["duration_s"] = round(time.monotonic() - started, 2)
        if note:
            entry["note"] = note
        self.manifest.phases.append(entry)
        self._flush()

    def _flush(self) -> None:
        """Write the manifest after every phase, not at the end.

        A manifest that only exists on success exists exactly when it is not
        needed.
        """
        (self.workdir / "run.json").write_text(
            self.manifest.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
        )

    # -- phases ---------------------------------------------------------
    def route(self, system_map: SystemMap) -> Roster:
        started = time.monotonic()
        roster = build_roster(system_map, self.budget)
        (self.workdir / "roster.json").write_text(roster.model_dump_json(indent=2), encoding="utf-8")
        for spec in roster.agents:
            self.manifest.agents.append(
                AgentRun(
                    agent_id=spec.agent_id,
                    role=spec.role,
                    spawn_reason=spec.spawn_reason,
                    model_tier=spec.model_tier.value,
                    context_slice=spec.context_slice,
                    slice_lines=spec.slice_lines,
                    status="spawned",  # registered before running, so a hang is visible
                )
            )
        for skip in roster.skipped:
            self.manifest.agents.append(
                AgentRun(agent_id=skip["agent_id"], status="skipped", spawn_reason=skip["reason"])
            )
        self._phase("route", "ok", started, f"{len(roster.agents)} agents, {len(roster.skipped)} skipped")
        return roster

    async def hunt(self, roster: Roster, bundles: dict[str, str], system_map: SystemMap) -> Quorum:
        started = time.monotonic()
        smap_json = system_map.model_dump_json(indent=2)
        sem = asyncio.Semaphore(self.max_concurrency)
        timeout = TIMEOUTS.get(self.budget, 480.0)

        async def run_one(spec) -> AgentResult:
            async with sem:
                agent = HuntAgent(spec, self.provider, timeout_s=timeout)
                bundle = bundles.get(spec.agent_id) or bundles.get("full", "")
                return await agent.run(bundle, smap_json)

        results = await asyncio.gather(
            *(run_one(spec) for spec in roster.agents), return_exceptions=True
        )

        by_id = {a.agent_id: a for a in self.manifest.agents}
        returned_valid = 0

        for spec, result in zip(roster.agents, results, strict=True):
            entry = by_id[spec.agent_id]
            if isinstance(result, BaseException):
                entry.status, entry.error = "failed", f"{type(result).__name__}: {result}"
                continue

            entry.attempts = result.attempts
            entry.duration_s = result.duration_s
            entry.markers = result.markers
            entry.records_emitted = len(result.records)

            accepted, rejected, _ = self.ledger.append_many(spec.agent_id, result.records)
            entry.records_valid = accepted

            if result.status in {"ok", "retried-ok"} and accepted:
                entry.status = result.status
                returned_valid += 1
            elif accepted:
                entry.status = "retried-ok"
                returned_valid += 1
            elif rejected:
                entry.status, entry.error = "quarantined", f"{rejected} record(s) failed schema"
            else:
                entry.status = result.status if result.status != "ok" else "invalid-output"
                entry.error = result.error

        expected = len(roster.agents)
        ratio = returned_valid / expected if expected else 0.0
        with_output = self.ledger.agents_with_output()
        missing_core = sorted(CORE_IDS - with_output)

        quorum = Quorum(
            expected_agents=expected,
            returned_valid=returned_valid,
            ratio=round(ratio, 3),
            threshold=QUORUM_THRESHOLD,
            # A core lane failing still leaves the ratio high on a big roster and
            # still means nobody checked the permission model.
            degraded=ratio < QUORUM_THRESHOLD or bool(missing_core),
            missing_lanes=[a.agent_id for a in self.manifest.agents
                           if a.status in {"failed", "timeout", "invalid-output", "quarantined"}],
        )
        self.manifest.quorum = quorum
        self._phase("hunt", "degraded" if quorum.degraded else "ok", started,
                    f"{returned_valid}/{expected} valid" + (f"; missing core: {missing_core}" if missing_core else ""))
        return quorum

    async def verify(self, slices: dict[str, str]) -> dict:
        started = time.monotonic()
        findings = self.ledger.read_all()
        verifier = AdversarialVerifier(self.provider, max_iterations=verify_iterations(self.budget))
        stats = await verifier.verify_all(findings, slices, budget=self.budget)
        self.manifest.verification = stats
        (self.workdir / "verified.json").write_text(
            json.dumps([f.model_dump(mode="json", exclude_none=True) for f in findings], indent=2),
            encoding="utf-8",
        )
        self._phase("verify", "ok", started, f"{stats['confirmed']} confirmed, {stats['refuted']} refuted")
        return stats

    def reduce_and_judge(self, system_map: SystemMap) -> dict:
        started = time.monotonic()
        findings = self.ledger.read_all()

        verified_path = self.workdir / "verified.json"
        if verified_path.exists():
            from .schemas import Finding

            findings = [Finding.model_validate(r) for r in json.loads(verified_path.read_text(encoding="utf-8"))]

        result = reduce_findings(findings)
        gaps, coverage = axis_coverage(system_map.hot_functions, findings)
        result["axis_gaps"] = gaps
        result["coverage"] = coverage
        self.manifest.coverage = {**coverage, **{
            k: result["completeness"][k]
            for k in ("unique_contract_function_raw", "unique_contract_function_final")
        }}

        # JUDGE: refutation is terminal; everything else gets its severity
        # clamped by the confidence the pipeline actually computed.
        for rec in result["reduced"]:
            conf = int(rec["judgment"]["confidence"])
            verdict = (rec.get("verification") or {}).get("verifier_verdict", "NOT-RUN")
            if verdict == "REFUTED":
                rec["judgment"]["final_severity"] = "rejected"
                rec["judgment"]["rationale"] = "Refuted by the adversarial verifier."
            else:
                rec["judgment"]["final_severity"] = severity_cap(rec["severity_claim"], conf)
                if rec["judgment"]["final_severity"] != rec["severity_claim"]:
                    rec["judgment"]["rationale"] = f"Severity capped by confidence {conf}."

        counts: dict[str, int] = {}
        for rec in result["reduced"]:
            sev = rec["judgment"]["final_severity"]
            counts[sev] = counts.get(sev, 0) + 1
        self.manifest.outcome = counts

        (self.workdir / "reduced.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        self._phase("reduce", "ok", started, f"{len(result['reduced'])} groups")
        return result

    # -- driver ---------------------------------------------------------
    async def run(self, system_map: SystemMap, bundles: dict[str, str], slices: dict[str, str] | None = None) -> dict:
        self._phase("preflight", "ok")
        roster = self.route(system_map)
        quorum = await self.hunt(roster, bundles, system_map)

        if quorum.ratio < ABORT_THRESHOLD:
            self._phase("report", "failed", note=f"quorum {quorum.ratio} below abort threshold")
            self.manifest.finished_at = _now()
            self._flush()
            return {
                "aborted": True,
                "reason": (
                    f"Only {quorum.returned_valid}/{quorum.expected_agents} agents returned valid output. "
                    "A findings list from a half-dead fan-out reads as a clean bill of health. "
                    "Re-run these lanes: " + ", ".join(quorum.missing_lanes)
                ),
                "manifest": self.manifest.model_dump(mode="json", exclude_none=True),
            }

        await self.verify(slices or {})
        result = self.reduce_and_judge(system_map)

        self.manifest.finished_at = _now()
        self._phase("report", "degraded" if quorum.degraded else "ok")
        result["manifest"] = self.manifest.model_dump(mode="json", exclude_none=True)
        result["degraded"] = quorum.degraded
        return result
