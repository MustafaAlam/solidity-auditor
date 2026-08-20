#!/usr/bin/env python3
"""
check_lenses.py — refuse to start an audit with blind agents.

Why this exists
---------------
`build_system_prompt()` used to return an empty string for a missing specialty
file, and the empty section was silently filtered out of the bundle. A routed
`oracle-expert` with no `oracle-expert-agent.md` therefore ran with the SOP and
the shared rules and *no lens at all* — and produced plausible, generic,
confidently-worded findings while the manifest recorded `status: ok`.

That is the worst failure mode this system can have. A crashed agent is
obvious. A blind agent looks exactly like a healthy one, and its silence on the
bug it was supposed to catch reads as a clean bill of health.

So: every lens the router can spawn is declared in MANIFEST.json, and this
script runs in PREFLIGHT, before a single token is spent.

Usage
-----
  python3 check_lenses.py                       # check everything in the manifest
  python3 check_lenses.py --roster roster.json  # check only what this run will spawn
  python3 check_lenses.py --warn-only           # report, exit 0 (CI bootstrap only)

Exit codes: 0 all present · 1 one or more missing · 2 manifest unreadable
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
AGENTS_DIR = HERE.parent / "hacking-agents"
MANIFEST = AGENTS_DIR / "MANIFEST.json"

# A file that exists but is a stub is still a blind agent. These thresholds are
# deliberately low - they catch truncation and placeholders, not brevity.
MIN_BYTES = 400
MIN_LINES = 12


def load_manifest() -> dict:
    if not MANIFEST.exists():
        print(f"FATAL: {MANIFEST} not found. Cannot verify lens coverage.", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def check(agent_ids: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    manifest = load_manifest()
    missing: list[dict] = []
    stubs: list[dict] = []

    for lens in manifest["lenses"]:
        if agent_ids is not None and lens["agent_id"] not in agent_ids:
            continue
        path = AGENTS_DIR / lens["file"]
        if not path.exists():
            missing.append(lens)
            continue
        text = path.read_text(encoding="utf-8")
        if len(text) < MIN_BYTES or len(text.splitlines()) < MIN_LINES:
            stubs.append({**lens, "bytes": len(text), "lines": len(text.splitlines())})

    return missing, stubs


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify every routable lens has a real specialty file.")
    ap.add_argument("--roster", type=pathlib.Path, help="Check only the agents in this roster.json.")
    ap.add_argument("--warn-only", action="store_true", help="Report and exit 0. Bootstrap only.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    agent_ids = None
    scope = "manifest"
    if args.roster and args.roster.exists():
        roster = json.loads(args.roster.read_text(encoding="utf-8"))
        agent_ids = {a["agent_id"] for a in roster.get("agents", [])}
        scope = f"roster ({len(agent_ids)} agents)"

    missing, stubs = check(agent_ids)

    if not missing and not stubs:
        if not args.quiet:
            total = len(agent_ids) if agent_ids else len(load_manifest()["lenses"])
            print(f"Lens check: {total}/{total} lenses present against {scope}.")
        return 0

    print(f"\n{'=' * 72}", file=sys.stderr)
    print("LENS CHECK FAILED — agents would run blind", file=sys.stderr)
    print("=" * 72, file=sys.stderr)

    if missing:
        print(f"\n{len(missing)} specialty file(s) MISSING:\n", file=sys.stderr)
        for lens in missing:
            print(f"  {lens['agent_id']:<24} {lens['file']:<34} ({lens['role']}, from {lens['origin']})", file=sys.stderr)
            if lens.get("purpose"):
                print(f"  {'':<24} would cover: {lens['purpose']}", file=sys.stderr)

    if stubs:
        print(f"\n{len(stubs)} specialty file(s) look like STUBS:\n", file=sys.stderr)
        for lens in stubs:
            print(f"  {lens['agent_id']:<24} {lens['bytes']}B / {lens['lines']} lines "
                  f"(min {MIN_BYTES}B / {MIN_LINES} lines)", file=sys.stderr)

    print(
        "\nA routed agent with no lens still returns findings, still reports status ok, and\n"
        "still counts toward quorum. Its silence on the bug it was meant to catch is\n"
        "indistinguishable from a clean result. Restore the files before running.\n",
        file=sys.stderr,
    )

    return 0 if args.warn_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
