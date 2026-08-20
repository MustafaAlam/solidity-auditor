"""Shared fixtures. Everything runs offline against the stub provider."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def da(block: str | None = None, bypass: str | None = None) -> dict:
    dims = {
        name: {"note": "Checked this dimension; nothing blocks the path.", "blocks": False}
        for name in ("guards", "reentrancy", "access", "by_design", "economic", "dry_run")
    }
    if block:
        dims[block] = {"note": "A guard partially mitigates this path.", "blocks": True}
        if bypass:
            dims[block]["bypass"] = bypass
    return {**dims, "verdict": "survives" if (not block or bypass) else "demote-to-lead"}


def finding(
    agent_id="access-control",
    contract="Vault",
    function="withdraw",
    bug_class="reentrancy",
    kind="FINDING",
    severity="high",
    fix=None,
    axes=None,
    **extra,
) -> dict:
    rec = {
        "schema_version": "3.0.0",
        "kind": kind,
        "agent_id": agent_id,
        "contract": contract,
        "file": f"src/{contract}.sol",
        "function": function,
        "bug_class": bug_class,
        "group_key": f"{contract}|{function}|{bug_class}",
        "lane": "accounting-entitlement",
        "severity_claim": severity,
        "axes": axes or ["theft", "accounting"],
        "description": f"{function} mishandles {bug_class}, letting any caller extract value from honest depositors.",
        "root_cause": f"{function} writes state after the external call.",
        "path": f"attacker -> {contract}.{function} -> external call -> re-entry -> drained balance",
        "fix": fix or {
            "label": "reorder",
            "summary": "Move the state write above the external call.",
            "add_lines": ["balances[msg.sender] = 0"],
        },
        "devils_advocate": da(),
    }
    if kind == "FINDING":
        rec["proof"] = {
            "kind": "numeric-trace",
            "content": "Deposit 1e18, re-enter withdraw twice, final balance 2e18 against 1e18 deposited.",
        }
    if severity in {"medium", "high", "critical"}:
        rec["poc"] = {"status": "sketch", "framework": "foundry", "code": "function testExploit() public {}"}
    rec.update(extra)
    return rec


@pytest.fixture
def sample_records() -> list[dict]:
    return [
        finding(),
        finding(agent_id="execution-trace", bug_class="cross-function-reentrancy"),
        finding(
            agent_id="numerical-gap",
            bug_class="rounding-direction",
            severity="medium",
            fix={"label": "round-direction", "summary": "Round shares up on burn.",
                 "add_lines": ["shares = mulDivUp(a, b, c)"]},
        ),
        finding(agent_id="oracle-expert", contract="PriceFeed", function="latestPrice",
                bug_class="stale-oracle", axes=["provenance", "accounting"]),
        finding(agent_id="numerical-gap", function="preview", bug_class="precision-loss",
                kind="LEAD", severity="low"),
    ]


@pytest.fixture
def vulnerable_source(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "contracts"
    (root / "src").mkdir(parents=True)
    (root / "test").mkdir()
    (root / "src" / "Vault.sol").write_text(
        """
pragma solidity ^0.8.20;
contract Vault {
    mapping(address => uint256) public balances;
    address public owner;
    function deposit() external payable { balances[msg.sender] += msg.value; }
    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount);
        (bool ok,) = msg.sender.call{value: amount}("");
        require(ok);
        balances[msg.sender] -= amount;
    }
    function setFee(uint256 f) external { fee = f; }
    uint256 public fee;
}
""".strip(),
        encoding="utf-8",
    )
    (root / "src" / "PriceFeed.sol").write_text(
        """
pragma solidity ^0.8.20;
interface AggregatorV3 { function latestRoundData() external view returns (uint80,int256,uint256,uint256,uint80); }
contract PriceFeed {
    AggregatorV3 public feed;
    function latestPrice() external view returns (int256) {
        (, int256 answer,,,) = feed.latestRoundData();
        return answer;
    }
}
""".strip(),
        encoding="utf-8",
    )
    # Excluded by scope rules; if this shows up in the map, ingest is broken.
    (root / "test" / "Vault.t.sol").write_text("contract VaultTest {}", encoding="utf-8")
    return root


@pytest.fixture
def stub_hunt_response(sample_records):
    """A response shaped the way a real model responds: prose, markers, fenced JSON."""
    lines = "\n".join(json.dumps(r) for r in sample_records[:2])
    return (
        "[Feynman: withdraw] This function hands money back to whoever asks, then writes down "
        "that they took it.\n"
        "[Socratic: Vault.sol:9 — why?] The developer believes the recipient cannot call back in.\n"
        "[Inversion: withdraw] Attacker deploys a receiver that calls withdraw again; "
        "attacker uses a 1 wei probe; attacker chains through a hook.\n\n"
        f"```jsonl\n{lines}\n```\n"
    )
