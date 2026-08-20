"""Pydantic models — the same contract the skill enforces in JSON Schema.

These mirror ``references/schemas/*.json`` exactly. Keeping the two in sync
matters: a record produced by the deployed service must be scoreable by the same
``evals/score.py`` and reducible by the same ``scripts/reduce.py``, or the two
halves of this project stop being comparable.

``tests/test_schema_parity.py`` asserts the field sets match.
"""

from __future__ import annotations

import enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "3.0.0"


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------
class Kind(str, enum.Enum):
    FINDING = "FINDING"
    LEAD = "LEAD"
    AXISGAP = "AXISGAP"
    COVERAGE_NOTE = "COVERAGE_NOTE"


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    informational = "informational"


class Axis(str, enum.Enum):
    theft = "theft"
    liveness = "liveness"
    accounting = "accounting"
    provenance = "provenance"
    boundary = "boundary"
    identity = "identity"


class Lane(str, enum.Enum):
    semantic_consistency = "semantic-consistency"
    callback_liveness = "callback-liveness"
    accounting_entitlement = "accounting-entitlement"
    token_oracle_statefulness = "token-oracle-statefulness"
    economic_differential = "economic-differential"
    adversarial_deep = "adversarial-deep"
    gap_seam = "gap-seam"
    domain_specialty = "domain-specialty"
    proof_tooling = "proof-tooling"
    platform_surface = "platform-surface"


class FixLabel(str, enum.Enum):
    validate = "validate"
    restrict = "restrict"
    allow_and_handle = "allow-and-handle"
    ban_path = "ban-path"
    reorder = "reorder"
    round_direction = "round-direction"
    redesign = "redesign"


class PocStatus(str, enum.Enum):
    sketch = "sketch"
    compiled = "compiled"
    passing = "passing"
    not_feasible = "not-feasible"


class VerifierVerdict(str, enum.Enum):
    CONFIRMED = "CONFIRMED"
    WEAKENED = "WEAKENED"
    REFUTED = "REFUTED"
    UNREACHABLE_CODE = "UNREACHABLE-CODE"
    NOT_RUN = "NOT-RUN"


class ModelTier(str, enum.Enum):
    triage = "triage"
    deep = "deep"
    verify = "verify"


# ---------------------------------------------------------------------------
# finding record
# ---------------------------------------------------------------------------
class DADimension(BaseModel):
    note: str = Field(min_length=5, max_length=300)
    blocks: bool
    bypass: str | None = None


class DevilsAdvocate(BaseModel):
    guards: DADimension
    reentrancy: DADimension
    access: DADimension
    by_design: DADimension
    economic: DADimension
    dry_run: DADimension
    verdict: Literal["survives", "demote-to-lead", "discard"]

    @model_validator(mode="after")
    def _blockers_need_a_bypass(self) -> DevilsAdvocate:
        """A record cannot claim 'survives' over a dimension it admits blocks it.

        This is the single rule that stops the Devil's Advocate block from
        becoming decoration - an agent must either name the concrete bypass or
        demote its own finding.
        """
        if self.verdict != "survives":
            return self
        for name in ("guards", "reentrancy", "access", "by_design", "economic", "dry_run"):
            dim: DADimension = getattr(self, name)
            if dim.blocks and not dim.bypass:
                raise ValueError(f"devils_advocate.{name} blocks but verdict='survives' with no bypass named")
        return self


class Proof(BaseModel):
    kind: Literal["numeric-trace", "state-sequence", "quoted-code", "counterexample"]
    content: str = Field(min_length=20)
    values: dict[str, str] = Field(default_factory=dict)


class Fix(BaseModel):
    label: FixLabel
    summary: str = Field(min_length=5, max_length=300)
    diff: str | None = None
    add_lines: list[str] = Field(default_factory=list)


class Poc(BaseModel):
    status: PocStatus
    framework: str | None = None
    code: str | None = None
    command: str | None = None
    result: str | None = None
    why_not: str | None = None

    @model_validator(mode="after")
    def _explain_infeasibility(self) -> Poc:
        if self.status is PocStatus.not_feasible and not self.why_not:
            raise ValueError("poc.status='not-feasible' requires a code-grounded why_not")
        if self.status in {PocStatus.sketch, PocStatus.compiled, PocStatus.passing} and not self.code:
            raise ValueError(f"poc.status='{self.status.value}' requires poc.code")
        return self


class Verification(BaseModel):
    verifier_verdict: VerifierVerdict = VerifierVerdict.NOT_RUN
    refutation: str = ""
    counter_evidence: str = ""
    iterations: int = 0
    residual_risk: str = ""


class Judgment(BaseModel):
    gate1_reachability: dict | None = None
    gate2_impact: dict | None = None
    gate3_code_defect: dict | None = None
    gate4_fixability: dict | None = None
    admin_amplifier: str | None = None
    final_severity: str | None = None
    confidence: int | None = None
    rationale: str | None = None


class Finding(BaseModel):
    schema_version: Literal["3.0.0"] = SCHEMA_VERSION
    kind: Kind
    agent_id: str = Field(pattern=r"^[a-z0-9-]+$")

    contract: str = Field(min_length=1)
    file: str | None = None
    function: str = Field(min_length=1)
    lines: list[int] = Field(default_factory=list, max_length=2)

    bug_class: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    group_key: str = ""

    lane: Lane
    domain: str | None = None
    axes: list[Axis] = Field(default_factory=list)

    severity_claim: Severity
    description: str = Field(min_length=10, max_length=600)
    root_cause: str | None = Field(default=None, max_length=400)
    path: str = Field(min_length=5)

    proof: Proof | None = None
    fix: Fix
    alternatives: list[Fix] = Field(default_factory=list)
    devils_advocate: DevilsAdvocate
    poc: Poc | None = None

    seam: str | None = None
    guard_gap: str | None = None

    # orchestrator-owned
    corroboration: list[str] = Field(default_factory=list)
    verification: Verification | None = None
    judgment: Judgment | None = None
    telemetry: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enforce_contract(self) -> Finding:
        # group_key is derived, not trusted. An agent that computes it wrongly
        # gets it corrected rather than silently splitting a dedup group.
        expected = f"{self.contract}|{self.function}|{self.bug_class}"
        if self.group_key != expected:
            self.group_key = expected

        if self.kind is Kind.FINDING:
            if self.proof is None:
                raise ValueError("kind=FINDING requires proof (emit kind=LEAD instead)")
            if not self.root_cause:
                raise ValueError("kind=FINDING requires root_cause")
            if not self.axes:
                raise ValueError("kind=FINDING requires at least one axis")

        if self.severity_claim in {Severity.medium, Severity.high, Severity.critical} and self.poc is None:
            raise ValueError(f"severity_claim={self.severity_claim.value} requires a poc block")

        return self


# ---------------------------------------------------------------------------
# system map (routing inputs only - the full map is larger)
# ---------------------------------------------------------------------------
class Evidence(BaseModel):
    """Every field here is a routing trigger. See core/router.py."""

    erc_surfaces: list[str] = Field(default_factory=list)
    eip_surfaces: list[str] = Field(default_factory=list)
    has_oracle: bool = False
    oracle_kinds: list[str] = Field(default_factory=list)
    has_lending: bool = False
    has_amm: bool = False
    has_vault: bool = False
    has_async_request_lifecycle: bool = False
    has_hooks: bool = False
    has_signatures: bool = False
    has_delegatecall: bool = False
    has_proxy_or_upgrade: bool = False
    has_transient_storage: bool = False
    has_assembly: bool = False
    has_crosschain: bool = False
    has_account_abstraction: bool = False
    has_fee_math: bool = False
    has_fixed_point: bool = False
    has_tokenomics: bool = False
    has_governance_timelock: bool = False
    has_nft_identity: bool = False
    external_dependencies: list[str] = Field(default_factory=list)


class HotFunction(BaseModel):
    contract: str
    function: str
    risk_weight: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    required_axes: list[Axis] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_axes(self) -> HotFunction:
        # An unbounded axis list turns a 40-function map into 240 coverage gaps
        # and teaches the reader to skip the section.
        if not self.required_axes:
            self.required_axes = (
                list(Axis) if self.risk_weight >= 0.6 else [Axis.theft, Axis.accounting, Axis.liveness]
            )
        return self


class Invariant(BaseModel):
    id: str
    statement: str
    kind: Literal["safety", "liveness", "functional", "accounting"] = "safety"
    confidence: Literal["stated-by-docs", "inferred-from-code", "speculative"] = "inferred-from-code"


class TrustBoundary(BaseModel):
    actor: str
    trust: Literal["trusted", "semi-trusted", "untrusted"] = "trusted"
    powers: list[str] = Field(default_factory=list)
    acquirable: bool = False


class Scope(BaseModel):
    files: list[str] = Field(default_factory=list)
    total_sloc: int = 0
    excluded: list[str] = Field(default_factory=list)
    solc_version: str | None = None
    build_system: Literal["foundry", "hardhat", "unknown", "none"] = "unknown"
    compiles: bool = False


class SystemMap(BaseModel):
    schema_version: Literal["3.0.0"] = SCHEMA_VERSION
    scope: Scope = Field(default_factory=Scope)
    contracts: list[dict] = Field(default_factory=list)
    value_flow: list[dict] = Field(default_factory=list)
    hot_functions: list[HotFunction] = Field(default_factory=list)
    evidence: Evidence = Field(default_factory=Evidence)
    invariants: list[Invariant] = Field(default_factory=list)
    trust_boundaries: list[TrustBoundary] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# roster / manifest
# ---------------------------------------------------------------------------
class AgentSpec(BaseModel):
    agent_id: str
    role: Literal["lane", "gap", "domain", "proof", "platform", "verifier"]
    spawn_reason: str
    model_tier: ModelTier = ModelTier.deep
    context_slice: Literal["full", "domain", "hot"] = "full"
    slice_files: list[str] = Field(default_factory=list)
    slice_lines: int = 0


class Roster(BaseModel):
    budget: Literal["quick", "standard", "deep", "exhaustive"] = "standard"
    slicing_enabled: bool = False
    agents: list[AgentSpec] = Field(default_factory=list)
    skipped: list[dict] = Field(default_factory=list)


class AgentRun(BaseModel):
    agent_id: str
    role: str = "lane"
    spawn_reason: str = ""
    model_tier: str | None = None
    context_slice: str | None = None
    slice_lines: int = 0
    status: Literal[
        "spawned", "ok", "retried-ok", "timeout", "invalid-output", "quarantined", "failed", "skipped"
    ] = "spawned"
    attempts: int = 0
    duration_s: float = 0.0
    records_emitted: int = 0
    records_valid: int = 0
    markers: dict[str, int] = Field(default_factory=dict)
    transport: Literal["local", "a2a"] = "local"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    error: str | None = None


class Quorum(BaseModel):
    expected_agents: int = 0
    returned_valid: int = 0
    ratio: float = 0.0
    threshold: float = 0.8
    degraded: bool = False
    missing_lanes: list[str] = Field(default_factory=list)


class RunManifest(BaseModel):
    schema_version: Literal["3.0.0"] = SCHEMA_VERSION
    run_id: str
    skill_version: str = "3.0.0"
    started_at: str
    finished_at: str | None = None
    config: dict = Field(default_factory=dict)
    phases: list[dict] = Field(default_factory=list)
    agents: list[AgentRun] = Field(default_factory=list)
    coverage: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    quorum: Quorum = Field(default_factory=Quorum)
    cost: dict = Field(default_factory=dict)
    outcome: dict = Field(default_factory=dict)
