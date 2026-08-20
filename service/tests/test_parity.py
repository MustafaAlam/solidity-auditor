"""Parity between the skill's scripts and the service's modules.

The two halves of this project must produce identical numbers on identical
input. If they drift, a scorecard from one is not comparable with the other and
the eval harness quietly stops meaning anything.

These tests exist to catch that drift at the moment it is introduced, which is
the only time it is cheap to fix.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
from conftest import finding

from audit_mas.core.reduction import POC_BONUS, PROOF_BASE, SYNONYM_CLUSTERS, VERIFIER_DELTA
from audit_mas.core.reduction import compute_confidence as service_confidence
from audit_mas.core.reduction import reduce_findings as service_reduce
from audit_mas.schemas import Finding

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "references" / "scripts"
SCHEMAS = ROOT / "references" / "schemas"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_reduce():
    return _load("script_reduce", SCRIPTS / "reduce.py")


@pytest.fixture(scope="module")
def script_validate():
    return _load("script_validate", SCRIPTS / "validate_findings.py")


# ---------------------------------------------------------------------------
def test_confidence_constants_match(script_reduce):
    assert script_reduce.PROOF_BASE == PROOF_BASE
    assert script_reduce.POC_BONUS == POC_BONUS
    assert script_reduce.VERIFIER_DELTA == VERIFIER_DELTA


def test_synonym_clusters_match(script_reduce):
    assert [sorted(c) for c in script_reduce.SYNONYM_CLUSTERS] == [sorted(c) for c in SYNONYM_CLUSTERS]


def test_confidence_agrees_record_by_record(script_reduce, sample_records):
    for rec in sample_records:
        model = Finding.model_validate(rec)
        # The script reads plain dicts; the service reads validated models. Same
        # arithmetic must come out of both.
        assert script_reduce.compute_confidence(rec, 1) == service_confidence(model, 1), rec["group_key"]
        assert script_reduce.compute_confidence(rec, 3) == service_confidence(model, 3), rec["group_key"]


def test_grouping_agrees(script_reduce, sample_records):
    script_out = script_reduce.reduce_records(sample_records)
    service_out = service_reduce([Finding.model_validate(r) for r in sample_records])

    assert {r["group_key"] for r in script_out["reduced"]} == {r["group_key"] for r in service_out["reduced"]}
    assert script_out["completeness"]["unique_contract_function_raw"] == \
        service_out["completeness"]["unique_contract_function_raw"]
    assert len(script_out["coexisting"]) == len(service_out["coexisting"])


def test_fix_fingerprints_agree(script_reduce):
    fix = {"label": "restrict", "summary": "Add onlyOwner.", "add_lines": ["require(msg.sender == owner);"]}
    from audit_mas.core.reduction import fix_fingerprint as service_fp
    from audit_mas.schemas import Fix

    assert script_reduce.fix_fingerprint(fix) == service_fp(Fix.model_validate(fix))


# ---------------------------------------------------------------------------
def test_validator_and_pydantic_agree_on_valid_records(script_validate, sample_records):
    schema = json.loads((SCHEMAS / "finding.schema.json").read_text())
    for rec in sample_records:
        assert script_validate.validate_record(rec, schema) == [], rec["group_key"]
        Finding.model_validate(rec)  # must not raise


@pytest.mark.parametrize(
    "mutate,expect",
    [
        (lambda r: r.pop("proof"), "proof"),
        (lambda r: r.update(group_key="wrong|key|here"), "group_key"),
        (lambda r: r.update(severity_claim="critical", poc=None) or r.pop("poc"), "poc"),
    ],
)
def test_validator_rejects_what_pydantic_rejects(script_validate, mutate, expect):
    schema = json.loads((SCHEMAS / "finding.schema.json").read_text())
    rec = finding()
    mutate(rec)
    errors = script_validate.validate_record(rec, schema)
    assert errors, f"script validator accepted a record it should reject ({expect})"
    assert any(expect in e for e in errors)


def test_schema_field_sets_match():
    """Every property in the JSON Schema must exist on the Pydantic model."""
    schema = json.loads((SCHEMAS / "finding.schema.json").read_text())
    schema_fields = set(schema["properties"])
    model_fields = set(Finding.model_fields)
    missing = schema_fields - model_fields
    assert not missing, f"Pydantic model is missing schema fields: {sorted(missing)}"


def test_system_map_schema_fields_match():
    from audit_mas.schemas import Evidence

    schema = json.loads((SCHEMAS / "system-map.schema.json").read_text())
    evidence_fields = set(schema["properties"]["evidence"]["properties"])
    assert evidence_fields == set(Evidence.model_fields), (
        "Evidence drives routing; a field present in one half and not the other "
        "silently changes which agents run."
    )
