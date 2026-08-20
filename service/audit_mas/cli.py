"""Command line entry point.

    python -m audit_mas.cli audit ./contracts --budget standard
    python -m audit_mas.cli map ./contracts
    python -m audit_mas.cli roster ./contracts --budget deep
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from .core.router import build_roster
from .ingest import build_bundle, build_system_map
from .orchestrator import Orchestrator
from .providers import from_env


def cmd_map(args: argparse.Namespace) -> int:
    smap, _ = build_system_map(pathlib.Path(args.path))
    print(smap.model_dump_json(indent=2))
    return 0


def cmd_roster(args: argparse.Namespace) -> int:
    smap, _ = build_system_map(pathlib.Path(args.path))
    roster = build_roster(smap, args.budget)
    print(f"Budget: {roster.budget}   slicing: {roster.slicing_enabled}   SLOC: {smap.scope.total_sloc}\n")
    print(f"{'agent':<26}{'role':<10}{'tier':<8}{'slice':<8}reason")
    print("-" * 100)
    for spec in roster.agents:
        print(f"{spec.agent_id:<26}{spec.role:<10}{spec.model_tier.value:<8}"
              f"{spec.context_slice:<8}{spec.spawn_reason}")
    for skip in roster.skipped:
        print(f"{skip['agent_id']:<26}{'SKIPPED':<10}{'':<8}{'':<8}{skip['reason']}")
    print(f"\n{len(roster.agents)} agents will run, {len(roster.skipped)} skipped.")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.path)
    smap, sources = build_system_map(root)

    if not args.yes:
        print(f"Scope: {len(smap.scope.files)} files, {smap.scope.total_sloc} SLOC")
        print(f"Evidence: {[k for k, v in smap.evidence.model_dump().items() if v is True]}")
        print(f"Hot functions: {len(smap.hot_functions)}")
        if input("\nProceed? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Aborted at the map gate.")
            return 1

    full = build_bundle(sources)
    roster = build_roster(smap, args.budget)
    bundles = {"full": full, **{spec.agent_id: full for spec in roster.agents}}
    slices = {f"{h.contract}": full for h in smap.hot_functions}

    orch = Orchestrator(from_env(), pathlib.Path(args.workdir), budget=args.budget)
    result = asyncio.run(orch.run(smap, bundles, slices))

    if result.get("aborted"):
        print(f"ABORTED: {result['reason']}", file=sys.stderr)
        return 2

    out = pathlib.Path(args.workdir) / "reduced.json"
    print(json.dumps({
        "run_id": orch.run_id,
        "degraded": result.get("degraded"),
        "groups": len(result["reduced"]),
        "completeness": result["completeness"],
        "coverage": result["coverage"],
        "output": str(out),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="audit_mas", description="Solidity audit multi-agent system.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("map", help="Print the SystemMapArtifact and exit.")
    p.add_argument("path")
    p.set_defaults(func=cmd_map)

    p = sub.add_parser("roster", help="Show which agents would run and why.")
    p.add_argument("path")
    p.add_argument("--budget", default="standard", choices=["quick", "standard", "deep", "exhaustive"])
    p.set_defaults(func=cmd_roster)

    p = sub.add_parser("audit", help="Run the full phase graph.")
    p.add_argument("path")
    p.add_argument("--budget", default="standard", choices=["quick", "standard", "deep", "exhaustive"])
    p.add_argument("--workdir", default=".audit-run")
    p.add_argument("--yes", action="store_true", help="Skip the map gate (required for unattended runs).")
    p.set_defaults(func=cmd_audit)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
