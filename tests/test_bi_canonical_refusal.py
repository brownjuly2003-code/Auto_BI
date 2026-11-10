"""A valid spec with an unrepresentable compiled payload leaves blocking evidence."""

from __future__ import annotations

import json

import pytest

from auto_bi.evidence.canonical import CanonicalizationError
from auto_bi.evidence.manifest import verify_bundle
from auto_bi.ir.spec import DashboardSpec
from auto_bi.ir.validate import validate_spec
from auto_bi.verification.release_gate import _compile
from tests.test_bi_contract_validation import CHECK_PATH, REF, run, spec
from tests.test_semantic_gateway import model


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**400, "surrogate"])
def test_canonical_refusal_is_block_with_evidence(value: object) -> None:
    raw = spec().model_dump(mode="python")
    if value == "surrogate":
        raw["title"] = "bad\ud800title"
    else:
        raw["charts"][0]["query"]["filters"] = [{"column": "revenue", "op": "=", "value": value}]
    dashboard = DashboardSpec.model_validate(raw)
    assert validate_spec(dashboard, model()) == []
    with pytest.raises(CanonicalizationError) as refused:
        _compile(dashboard, model())
    report, store = run({REF: dashboard})
    check = next(c for c in report.checks if c.check_id == "bi.contract")
    assert check.status.value == "block"
    assert check.summary_code == "bi.compile_refused"
    payload = json.loads(store.get(report.run_id, CHECK_PATH))
    assert payload["compiled"] == []
    assert payload["refused"] == [
        {"dashboard": REF, "refusal": f"CanonicalizationError: {refused.value}"}
    ]
    assert check.evidence_refs
    assert verify_bundle(store, report.run_id) == []
