"""INGEST — scope resolution, evidence extraction, bundling.

The evidence extraction here is deliberately cheap: regex over source. It runs
before any model call and it decides the roster, so a false negative costs a
whole specialty. Every heuristic below therefore errs toward *firing* — a
spurious specialist costs one agent's tokens, a missing one costs a bug class.

An LLM MAP phase should refine and correct this. It should not replace it: a
regex that always runs beats a model call that sometimes hallucinates a lending
protocol into an ERC-20.
"""

from __future__ import annotations

import pathlib
import re

from .schemas import Evidence, HotFunction, Scope, SystemMap

EXCLUDE_DIRS = {"interfaces", "lib", "mocks", "test", "tests", "script", "scripts", "node_modules", "out", "cache"}
EXCLUDE_FILES = re.compile(r"(\.t\.sol|Test.*\.sol|.*Mock.*\.sol|.*Mock\.sol)$")

PATTERNS: dict[str, list[str]] = {
    "has_oracle": [r"latestRoundData", r"AggregatorV3", r"getPrice", r"priceProvider", r"\bNAV\b", r"twap", r"oracle"],
    "has_lending": [r"\bborrow\b", r"\brepay\b", r"liquidat", r"\bLTV\b", r"collateralFactor", r"accrueInterest", r"healthFactor"],
    "has_amm": [r"swap", r"addLiquidity", r"removeLiquidity", r"reserve0", r"sqrtPrice", r"\btick\b", r"\bbin\b"],
    "has_vault": [r"ERC4626", r"totalAssets", r"convertToShares", r"previewDeposit", r"\bshares\b"],
    "has_async_request_lifecycle": [r"requestDeposit", r"requestRedeem", r"pendingDeposit", r"claimable", r"ERC7540"],
    "has_hooks": [r"beforeSwap", r"afterSwap", r"_beforeTokenTransfer", r"\bhook", r"IHooks"],
    "has_signatures": [r"ecrecover", r"ECDSA", r"EIP712", r"DOMAIN_SEPARATOR", r"\bpermit\b", r"SignatureChecker"],
    "has_delegatecall": [r"delegatecall"],
    "has_proxy_or_upgrade": [r"upgradeTo", r"_authorizeUpgrade", r"initializer", r"__gap", r"ERC1967", r"UUPS", r"Proxy"],
    "has_transient_storage": [r"\btstore\b", r"\btload\b", r"\btransient\b"],
    "has_assembly": [r"\bassembly\b"],
    "has_crosschain": [r"chainid", r"chainId", r"lzReceive", r"_ccipReceive", r"\bbridge", r"sequencerUptime", r"messenger"],
    "has_account_abstraction": [r"validateUserOp", r"UserOperation", r"EntryPoint", r"7702", r"tx\.origin\s*==\s*msg\.sender", r"extcodesize", r"code\.length\s*==\s*0"],
    "has_fee_math": [r"\bfee\b", r"feeBps", r"BASIS_POINTS", r"\bBPS\b", r"protocolFee"],
    "has_fixed_point": [r"\bWAD\b", r"\bRAY\b", r"1e18", r"1e27", r"Q64", r"Q96", r"mulDiv", r"FixedPoint"],
    "has_tokenomics": [r"emission", r"vesting", r"rewardPerToken", r"\bveToken", r"claimRewards", r"distribut"],
    "has_governance_timelock": [r"Timelock", r"\bqueue\b.*\bexecute\b", r"Governor", r"propose\("],
    "has_nft_identity": [r"ERC721", r"ownerOf", r"tokenURI", r"\btokenId\b"],
}

ERC_PATTERN = re.compile(r"\bERC[- ]?(\d{2,5})\b", re.IGNORECASE)
EIP_PATTERN = re.compile(r"\bEIP[- ]?(\d{2,5})\b", re.IGNORECASE)
FUNC_PATTERN = re.compile(
    r"function\s+(\w+)\s*\(([^)]*)\)\s*([^{;]*)", re.MULTILINE
)
CONTRACT_PATTERN = re.compile(r"^\s*(contract|library|abstract contract|interface)\s+(\w+)", re.MULTILINE)


def in_scope(path: pathlib.Path) -> bool:
    if EXCLUDE_FILES.search(path.name):
        return False
    return not any(part in EXCLUDE_DIRS for part in path.parts)


def collect_sources(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.sol") if in_scope(p.relative_to(root)))


def extract_evidence(sources: dict[str, str]) -> Evidence:
    blob = "\n".join(sources.values())
    flags = {
        field: any(re.search(pat, blob, re.IGNORECASE) for pat in pats)
        for field, pats in PATTERNS.items()
    }
    ev = Evidence(**flags)
    ev.erc_surfaces = sorted({f"ERC{m}" for m in ERC_PATTERN.findall(blob)})
    ev.eip_surfaces = sorted({f"EIP{m}" for m in EIP_PATTERN.findall(blob)})

    # EIP-1153 and EIP-7702 usage frequently appears without the number written
    # anywhere. Backfill from the behavioural evidence so routing still fires.
    if ev.has_transient_storage and not any("1153" in s for s in ev.eip_surfaces):
        ev.eip_surfaces.append("EIP1153")
    if ev.has_account_abstraction and not any("7702" in s or "4337" in s for s in ev.eip_surfaces):
        ev.eip_surfaces.append("EIP7702")
    return ev


def rank_hot_functions(sources: dict[str, str], limit: int = 30) -> list[HotFunction]:
    """Cheap static ranking: entry-point status, value movement, state writes.

    This is a starting point for the MAP phase to refine, not a replacement for
    it. It exists so routing and coverage work even in a fully headless run.
    """
    hot: list[HotFunction] = []
    for path, text in sources.items():
        contracts = CONTRACT_PATTERN.findall(text)
        contract = contracts[0][1] if contracts else pathlib.Path(path).stem

        for match in FUNC_PATTERN.finditer(text):
            name, _params, modifiers = match.groups()
            mods = modifiers or ""
            if "private" in mods or "internal" in mods:
                continue
            if re.search(r"\b(view|pure)\b", mods):
                continue

            body = text[match.end(): match.end() + 1500]
            weight, reasons = 0.3, ["external entry point"]

            if "payable" in mods:
                weight += 0.2
                reasons.append("payable")
            if re.search(r"(transfer|transferFrom|call\{value|send\()", body):
                weight += 0.25
                reasons.append("moves value")
            if re.search(r"\w+\[[^\]]+\]\s*(=|\+=|-=)", body):
                weight += 0.15
                reasons.append("writes mapping state")
            if re.search(r"\.call\(|\.delegatecall\(|\.staticcall\(", body):
                weight += 0.15
                reasons.append("external call")
            if not re.search(r"only\w+|nonReentrant|whenNotPaused", mods):
                weight += 0.1
                reasons.append("unguarded")

            hot.append(
                HotFunction(
                    contract=contract, function=name,
                    risk_weight=round(min(1.0, weight), 2), reasons=reasons,
                )
            )

    hot.sort(key=lambda h: -h.risk_weight)
    return hot[:limit]


def build_system_map(root: pathlib.Path, *, compiles: bool = False) -> tuple[SystemMap, dict[str, str]]:
    root = pathlib.Path(root)
    paths = collect_sources(root)
    sources = {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace") for p in paths}
    sloc = sum(len(t.splitlines()) for t in sources.values())

    smap = SystemMap(
        scope=Scope(
            files=list(sources),
            total_sloc=sloc,
            build_system="foundry" if (root / "foundry.toml").exists()
            else "hardhat" if list(root.glob("hardhat.config.*")) else "none",
            compiles=compiles,
        ),
        evidence=extract_evidence(sources),
        hot_functions=rank_hot_functions(sources),
        open_questions=[
            "Static ingest only — confirm the evidence flags and hot-function ranking before the hunt.",
        ],
    )
    return smap, sources


def build_bundle(sources: dict[str, str], files: list[str] | None = None) -> str:
    selected = files or list(sources)
    return "\n\n".join(
        f"### {path}\n\n```solidity\n{sources[path]}\n```"
        for path in selected if path in sources
    )
