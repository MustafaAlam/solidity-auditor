#!/usr/bin/env python3
"""
validate_findings.py — the gate between agent output and the report.

Why this exists
---------------
In v2.7 an agent's output was prose that the orchestrator re-read and
interpreted. One malformed block silently vanished from the report and nobody
could tell. In v3 every agent writes JSON Lines into the ledger and this script
is the only thing allowed to admit a record.

Contract
--------
  in : one or more .jsonl files (one finding record per line)
  out: <ledger>/valid.jsonl        records that passed
       <ledger>/quarantine.jsonl   records that failed, each with `_errors`
       exit 0 always (quarantine is a normal outcome, not a crash) unless
       --strict is passed, in which case any quarantined record exits 1.

Usage
-----
  python3 validate_findings.py --ledger .audit-x/ledger
  python3 validate_findings.py --ledger .audit-x/ledger --strict
  python3 validate_findings.py --file agent-oracle.jsonl --schema ../schemas/finding.schema.json

Dependencies: stdlib only. Uses `jsonschema` when importable for full draft
2020-12 checking, otherwise falls back to the built-in structural validator
below, which enforces every rule the report actually depends on.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections.abc import Iterable
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_SCHEMA = HERE.parent / "schemas" / "finding.schema.json"

SEVERITIES = {"critical", "high", "medium", "low", "informational"}
NEEDS_POC = {"critical", "high", "medium"}
KINDS = {"FINDING", "LEAD", "AXISGAP", "COVERAGE_NOTE"}
AXES = {"theft", "liveness", "accounting", "provenance", "boundary", "identity"}
DA_DIMENSIONS = ("guards", "reentrancy", "access", "by_design", "economic", "dry_run")
FIX_LABELS = {
    "validate",
    "restrict",
    "allow-and-handle",
    "ban-path",
    "reorder",
    "round-direction",
    "redesign",
}
KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# --------------------------------------------------------------------------
# Fallback validator — deliberately checks the invariants the pipeline relies
# on rather than the whole schema. Keep these in sync with finding.schema.json.
# --------------------------------------------------------------------------
def structural_errors(rec: Any) -> list[str]:
    e: list[str] = []
    if not isinstance(rec, dict):
        return ["record is not a JSON object"]

    if rec.get("schema_version") != "3.0.0":
        e.append(f"schema_version must be '3.0.0', got {rec.get('schema_version')!r}")

    kind = rec.get("kind")
    if kind not in KINDS:
        e.append(f"kind must be one of {sorted(KINDS)}, got {kind!r}")

    for field in ("agent_id", "contract", "function", "bug_class", "group_key", "description", "path"):
        val = rec.get(field)
        if not isinstance(val, str) or not val.strip():
            e.append(f"{field} is required and must be a non-empty string")

    if isinstance(rec.get("agent_id"), str) and not KEBAB.match(rec["agent_id"]):
        e.append("agent_id must be kebab-case")
    if isinstance(rec.get("bug_class"), str) and not KEBAB.match(rec["bug_class"]):
        e.append("bug_class must be kebab-case")

    # group_key is the dedup primary key. A wrong one silently splits or merges
    # findings, so it is checked against its own components, not just parsed.
    gk = rec.get("group_key")
    if isinstance(gk, str):
        expected = f"{rec.get('contract')}|{rec.get('function')}|{rec.get('bug_class')}"
        if gk != expected:
            e.append(f"group_key must equal '<contract>|<function>|<bug_class>' ({expected!r}), got {gk!r}")

    sev = rec.get("severity_claim")
    if sev not in SEVERITIES:
        e.append(f"severity_claim must be one of {sorted(SEVERITIES)}, got {sev!r}")

    # Devil's Advocate: the whole point is that it cannot be skipped.
    da = rec.get("devils_advocate")
    if not isinstance(da, dict):
        e.append("devils_advocate is required and must be an object")
    else:
        for dim in DA_DIMENSIONS:
            d = da.get(dim)
            if not isinstance(d, dict):
                e.append(f"devils_advocate.{dim} missing")
                continue
            if not isinstance(d.get("note"), str) or len(d.get("note", "")) < 5:
                e.append(f"devils_advocate.{dim}.note must be a real sentence")
            if not isinstance(d.get("blocks"), bool):
                e.append(f"devils_advocate.{dim}.blocks must be a boolean")
            if d.get("blocks") is True and da.get("verdict") == "survives" and not d.get("bypass"):
                e.append(
                    f"devils_advocate.{dim} blocks but verdict is 'survives' with no bypass named"
                )
        if da.get("verdict") not in {"survives", "demote-to-lead", "discard"}:
            e.append("devils_advocate.verdict invalid")

    fix = rec.get("fix")
    if not isinstance(fix, dict):
        e.append("fix is required and must be an object")
    else:
        if fix.get("label") not in FIX_LABELS:
            e.append(f"fix.label must be one of {sorted(FIX_LABELS)}, got {fix.get('label')!r}")
        if not isinstance(fix.get("summary"), str) or len(fix.get("summary", "")) < 5:
            e.append("fix.summary must be a real sentence")

    # A FINDING without proof is a LEAD wearing a costume.
    if kind == "FINDING":
        proof = rec.get("proof")
        if not isinstance(proof, dict) or len(str(proof.get("content", ""))) < 20:
            e.append("kind=FINDING requires proof.content of at least 20 chars (else emit kind=LEAD)")
        if not isinstance(rec.get("root_cause"), str) or not rec.get("root_cause"):
            e.append("kind=FINDING requires root_cause")
        axes = rec.get("axes")
        if not isinstance(axes, list) or not axes:
            e.append("kind=FINDING requires a non-empty axes array")
        elif not set(axes) <= AXES:
            e.append(f"axes contains unknown values: {sorted(set(axes) - AXES)}")

    # PoC discipline for Medium+.
    if sev in NEEDS_POC:
        poc = rec.get("poc")
        if not isinstance(poc, dict):
            e.append(f"severity_claim={sev} requires a poc object")
        else:
            status = poc.get("status")
            if status not in {"sketch", "compiled", "passing", "not-feasible"}:
                e.append("poc.status invalid")
            if status == "not-feasible" and not poc.get("why_not"):
                e.append("poc.status='not-feasible' requires a code-grounded why_not")
            if status in {"sketch", "compiled", "passing"} and not poc.get("code"):
                e.append(f"poc.status='{status}' requires poc.code")

    # Agents must not write fields the orchestrator owns.
    for owned in ("verification", "judgment", "corroboration"):
        if owned in rec and rec.get("kind") != "AXISGAP":
            e.append(f"{owned} is orchestrator-owned; agents must not set it")

    return e


def jsonschema_errors(rec: Any, schema: dict) -> list[str] | None:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return None
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}" for err in validator.iter_errors(rec)]


def validate_record(rec: Any, schema: dict | None) -> list[str]:
    """Schema errors and structural errors are unioned, not alternated.

    jsonschema catches shape violations; structural_errors catches the
    cross-field rules (group_key consistency, DA bypass, PoC discipline) that
    are cheaper to express in Python than in JSON Schema. Running both means a
    missing `jsonschema` install degrades coverage rather than removing it.
    """
    errs: list[str] = []
    if schema is not None:
        js = jsonschema_errors(rec, schema)
        if js:
            errs.extend(js)
    errs.extend(structural_errors(rec))
    # de-duplicate while preserving order
    seen: set[str] = set()
    return [x for x in errs if not (x in seen or seen.add(x))]


def iter_lines(paths: Iterable[pathlib.Path]):
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw or raw.startswith("//"):
                    continue
                yield p, lineno, raw


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate agent finding records against the v3 schema.")
    ap.add_argument("--ledger", type=pathlib.Path, help="Directory of *.jsonl agent outputs.")
    ap.add_argument("--file", type=pathlib.Path, action="append", default=[], help="Individual .jsonl file(s).")
    ap.add_argument("--schema", type=pathlib.Path, default=DEFAULT_SCHEMA)
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if anything was quarantined.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    paths: list[pathlib.Path] = list(args.file)
    if args.ledger:
        paths.extend(sorted(p for p in args.ledger.glob("*.jsonl") if p.name not in {"valid.jsonl", "quarantine.jsonl"}))
    if not paths:
        print("no input files; pass --ledger or --file", file=sys.stderr)
        return 2

    schema = None
    if args.schema.exists():
        schema = json.loads(args.schema.read_text(encoding="utf-8"))

    valid: list[dict] = []
    quarantine: list[dict] = []

    for path, lineno, raw in iter_lines(paths):
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            quarantine.append({"_source": f"{path.name}:{lineno}", "_errors": [f"invalid JSON: {exc}"], "_raw": raw[:800]})
            continue
        errs = validate_record(rec, schema)
        if errs:
            rec_copy = dict(rec) if isinstance(rec, dict) else {"_raw": raw[:800]}
            rec_copy["_source"] = f"{path.name}:{lineno}"
            rec_copy["_errors"] = errs
            quarantine.append(rec_copy)
        else:
            rec["_source"] = f"{path.name}:{lineno}"
            valid.append(rec)

    out_dir = args.ledger if args.ledger else paths[0].parent
    (out_dir / "valid.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in valid), encoding="utf-8"
    )
    (out_dir / "quarantine.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in quarantine), encoding="utf-8"
    )

    if not args.quiet:
        print(f"Validation: {len(valid)} valid, {len(quarantine)} quarantined -> {out_dir}")
        by_agent: dict[str, int] = {}
        for r in quarantine:
            by_agent[str(r.get("agent_id", "?"))] = by_agent.get(str(r.get("agent_id", "?")), 0) + 1
        for agent, n in sorted(by_agent.items(), key=lambda kv: -kv[1]):
            print(f"  quarantined {n:>3}  agent={agent}")
        for r in quarantine[:5]:
            print(f"  e.g. {r.get('_source')}: {r['_errors'][0]}")

    return 1 if (args.strict and quarantine) else 0


if __name__ == "__main__":
    raise SystemExit(main())
