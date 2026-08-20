"""Adversarial verifier — the Generator/Critic loop.

The critic is structurally separate from the generator and receives deliberately
narrow context: the claim and the code it names, never the hunting agent's
reasoning. Passing that reasoning along would reimport exactly the bias this
component exists to remove.

Its incentive is inverted on purpose. It is asked to *kill* the finding, and a
claim that survives an honest attempt to destroy it is worth more than one
blessed by the agent that authored it.
"""

from __future__ import annotations

import asyncio
import json
import time

from ..schemas import Finding, ModelTier, Severity, Verification, VerifierVerdict
from .base import DEFAULT_MODELS, LLMProvider, extract_records, load_reference

VERIFY_THRESHOLDS: dict[str, set[Severity]] = {
    "quick": {Severity.critical, Severity.high, Severity.medium},
    "standard": {Severity.critical, Severity.high, Severity.medium},
    "deep": {Severity.critical, Severity.high, Severity.medium, Severity.low},
    "exhaustive": set(Severity),
}

TERMINAL = {VerifierVerdict.CONFIRMED, VerifierVerdict.REFUTED, VerifierVerdict.UNREACHABLE_CODE}


class AdversarialVerifier:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        models: dict[ModelTier, str] | None = None,
        max_iterations: int = 2,
        timeout_s: float = 300.0,
    ):
        self.provider = provider
        self.models = models or DEFAULT_MODELS
        self.max_iterations = max_iterations
        self.timeout_s = timeout_s
        self.system = load_reference("hacking-agents", "adversarial-verifier-agent.md")
        # Verification is a real slice of the bill; the manifest should say so.
        self.input_tokens = 0
        self.output_tokens = 0

    def _prompt(self, finding: Finding, code_slice: str) -> str:
        claim = finding.model_dump(
            mode="json",
            exclude_none=True,
            # The verifier judges the claim, not its provenance or its prior
            # verdicts. Stripping these keeps the attack honest.
            exclude={"verification", "judgment", "corroboration", "telemetry"},
        )
        return (
            "THE CLAIM\n```json\n" + json.dumps(claim, indent=2) + "\n```\n\n"
            "THE CODE\n```solidity\n" + code_slice + "\n```\n\n"
            "Attack it: reachability, guards, arithmetic, rationality, impact, design.\n"
            "Return ONE JSON object with keys: verifier_verdict, refutation, counter_evidence, residual_risk.\n"
            "`refutation` is required even when you return CONFIRMED — write the best case against the finding.\n"
        )

    async def verify_one(self, finding: Finding, code_slice: str) -> Verification:
        verification = Verification()
        model = self.models.get(ModelTier.verify, self.models[ModelTier.deep])

        for iteration in range(1, self.max_iterations + 1):
            verification.iterations = iteration
            try:
                completion = await asyncio.wait_for(
                    self.provider.complete(
                        system=self.system,
                        prompt=self._prompt(finding, code_slice),
                        model=model,
                        max_tokens=4000,
                    ),
                    timeout=self.timeout_s,
                )
            except (TimeoutError, Exception):
                verification.verifier_verdict = VerifierVerdict.NOT_RUN
                verification.refutation = "Verifier did not complete; treat this finding as unverified."
                return verification

            self.input_tokens += completion.input_tokens
            self.output_tokens += completion.output_tokens
            parsed = _parse_verdict(completion.text)
            if parsed is None:
                continue

            verification.verifier_verdict = parsed["verdict"]
            verification.refutation = parsed.get("refutation", "")
            verification.counter_evidence = parsed.get("counter_evidence", "")
            verification.residual_risk = parsed.get("residual_risk", "")

            if parsed["verdict"] in TERMINAL:
                break
            # WEAKENED gets exactly one more pass; the loop ceiling is what stops
            # a critic loop from burning a budget arguing with itself.

        if verification.verifier_verdict is VerifierVerdict.NOT_RUN and not verification.refutation:
            verification.refutation = "Verifier produced no parseable verdict; treat as unverified."
        return verification

    async def verify_all(
        self,
        findings: list[Finding],
        slices: dict[str, str],
        budget: str = "standard",
        concurrency: int = 6,
    ) -> dict:
        """Verify every candidate at or above the budget's threshold.

        Runs concurrently but bounded — an unbounded fan-out here is how a
        verification phase costs more than the hunt it is checking.
        """
        threshold = VERIFY_THRESHOLDS.get(budget, VERIFY_THRESHOLDS["standard"])
        targets = [f for f in findings if f.severity_claim in threshold]
        sem = asyncio.Semaphore(concurrency)
        started = time.monotonic()

        async def one(f: Finding) -> None:
            async with sem:
                f.verification = await self.verify_one(f, slices.get(f.group_key, slices.get(f.contract, "")))

        await asyncio.gather(*(one(f) for f in targets))

        for f in findings:
            if f.verification is None:
                f.verification = Verification(
                    verifier_verdict=VerifierVerdict.NOT_RUN,
                    refutation=f"Below the {budget} verification threshold; not independently checked.",
                )

        tally = {v.value: 0 for v in VerifierVerdict}
        for f in findings:
            tally[f.verification.verifier_verdict.value] += 1

        return {
            "candidates_sent": len(targets),
            "confirmed": tally["CONFIRMED"],
            "weakened": tally["WEAKENED"],
            "refuted": tally["REFUTED"],
            "unreachable": tally["UNREACHABLE-CODE"],
            "not_run": tally["NOT-RUN"],
            "duration_s": round(time.monotonic() - started, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _parse_verdict(text: str) -> dict | None:
    for obj in extract_records(text) or _loose_objects(text):
        raw = str(obj.get("verifier_verdict", "")).strip().upper().replace("_", "-")
        try:
            return {**obj, "verdict": VerifierVerdict(raw)}
        except ValueError:
            continue
    return None


def _loose_objects(text: str) -> list[dict]:
    """extract_records() requires a bug_class; verifier output has none."""
    import re

    out: list[dict] = []
    for block in re.findall(r"\{[^{}]*\"verifier_verdict\"[^{}]*\}", text, re.DOTALL):
        try:
            out.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return out
