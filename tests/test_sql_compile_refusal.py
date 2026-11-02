"""A refused metric leaves evidence and does not prevent other metrics compiling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from sqlglot import exp

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.decision.validate import ValidationReport
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.manifest import read_bundle, verify_bundle
from auto_bi.evidence.store import ArtifactStore, FilesystemArtifactStore
from auto_bi.semantic.gateway import SemanticGap, SemanticSnapshot, _build_graph
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.severity import Severity
from auto_bi.verification import sql_contract
from auto_bi.verification.environment import questions_for
from auto_bi.verification.release_gate import GateInputs, run_gate
from tests.test_bi_contract_validation import REF, MemoryStore, run, spec
from tests.test_decision_verify import product_contract
from tests.test_semantic_gateway import model as bi_model
from tests.test_sql_contract import REVENUE_REF, aggregate_contract, postgres_model, semantic_model

OTHER_REF = "metric://sales/other@3"
REFUSED_NAME = "bad\0name"


def inputs_for(field: str, dialect: str) -> GateInputs:
    raw = aggregate_contract(budgets={"max_scanned_rows": 1000})
    if field == "grain":
        raw["grain"]["keys"] = [REFUSED_NAME]
    else:
        raw["formula"]["aggregate"]["column"] = REFUSED_NAME
    document = yaml.safe_dump(raw, sort_keys=False)
    assert '"bad\\0name"' in document
    refused = MetricContract.model_validate(yaml.safe_load(document))
    other_raw = aggregate_contract(budgets={"max_scanned_rows": 2000})
    other_raw["metadata"]["id"] = "metric://sales/other"
    other = MetricContract.model_validate(yaml.safe_load(yaml.safe_dump(other_raw)))
    model = semantic_model() if dialect == "clickhouse" else postgres_model()
    gaps: list[SemanticGap] = []
    graph = _build_graph(model, [refused, other], gaps)
    digest = sha256_digest(graph.model_dump(mode="json"))
    snapshot = SemanticSnapshot(
        source_kind="native_model_yaml",
        source_version="1",
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
        gaps=gaps,
    )
    product_raw = product_contract()
    product_raw["scope"]["metrics"] = [REVENUE_REF, OTHER_REF]
    product = DecisionProductSpec.model_validate(product_raw)
    return GateInputs(
        product=product,
        validation=ValidationReport(verdict="pass"),
        base=snapshot,
        candidate=snapshot,
        candidate_model=model,
        validated_metrics={node.metadata.id: node for node in (refused, other)},
    )


@pytest.mark.parametrize("field", ["grain", "aggregate"])
@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_refused_metric_is_advisory_and_other_metric_compiles(field: str, dialect: str) -> None:
    inputs = inputs_for(field, dialect)
    compiled = sql_contract.compile_metric_queries(inputs.candidate, inputs.product.scope.metrics)
    assert list(compiled.by_metric()) == [OTHER_REF]
    assert compiled.queries[0].dialect == dialect
    assert len(compiled.findings) == 1
    finding = compiled.findings[0]
    assert finding.code == "sql.compile_refused"
    assert finding.severity is Severity.ADVISORY
    assert finding.subject == REVENUE_REF
    assert repr(REFUSED_NAME) in finding.detail
    assert "NUL or surrogate" in finding.detail
    assert "\0" not in finding.detail


def assert_gate_bundle(field: str, dialect: str, store: ArtifactStore) -> None:
    inputs = inputs_for(field, dialect)
    report = run_gate(inputs, store=store, created_at="2026-11-01T00:00:00Z")
    bundle = read_bundle(store, report.run_id)
    assert bundle.run_id == report.run_id
    assert verify_bundle(store, report.run_id) == []
    check = next(item for item in report.checks if item.check_id == "sql.compile")
    assert check.status.value == "advisory"
    assert check.summary_code == "sql.partially_compiled"
    payload = json.loads(store.get(report.run_id, "checks/check-sql.compile.json"))
    for side in ("base", "candidate"):
        assert [query["metric_ref"] for query in payload[side]["queries"]] == [OTHER_REF]
        assert payload[side]["findings"] == [
            {
                "code": "sql.compile_refused",
                "severity": "advisory",
                "subject": REVENUE_REF,
                "detail": (
                    f"generated SQL text {REFUSED_NAME!r} "
                    "cannot contain NUL or surrogate characters"
                ),
            }
        ]
    assert next(c for c in report.checks if c.check_id == "evidence.bundle").summary_code == (
        "evidence.verified"
    )


@pytest.mark.parametrize("field", ["grain", "aggregate"])
@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_refused_metric_gate_writes_bundle(field: str, dialect: str) -> None:
    assert_gate_bundle(field, dialect, MemoryStore())


@pytest.mark.parametrize("field", ["grain", "aggregate"])
@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_refused_metric_gate_writes_files(tmp_path: Path, field: str, dialect: str) -> None:
    assert_gate_bundle(field, dialect, FilesystemArtifactStore(tmp_path))
    assert len(list(tmp_path.glob("*/manifest.json"))) == 1


@pytest.mark.parametrize("field", ["grain", "aggregate"])
@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
def test_refused_metric_keeps_empty_plan_question(field: str, dialect: str) -> None:
    inputs = inputs_for(field, dialect)
    plans = questions_for(inputs.product, inputs.candidate).plans
    assert [plan.metric_ref for plan in plans] == [REVENUE_REF, OTHER_REF]
    assert plans[0].sql == plans[0].dialect == ""
    assert plans[0].table == "dm.sales_daily"
    assert plans[0].budgets.max_scanned_rows == 1000
    assert plans[1].sql
    assert plans[1].dialect == dialect


def test_unrelated_render_value_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = inputs_for("grain", "clickhouse")
    error = ValueError("compiler defect")

    def broken_render(expression: exp.Expression, *, dialect: str) -> str:
        raise error

    monkeypatch.setattr(sql_contract, "render_sql", broken_render)
    with pytest.raises(ValueError, match="compiler defect") as raised:
        sql_contract.compile_metric_queries(inputs.candidate, [OTHER_REF])
    assert raised.value is error


@pytest.mark.parametrize("target", ["superset", "datalens"])
def test_renderer_refusal_remains_a_bi_finding(target: str) -> None:
    model = bi_model()
    column = next(c for c in model.tables[0].columns if c.name == "revenue")
    column.name = REFUSED_NAME
    dashboard = spec(target, column=REFUSED_NAME)
    report, store = run({REF: dashboard}, candidate_model=model)
    check = next(item for item in report.checks if item.check_id == "bi.contract")
    assert check.summary_code == "bi.compile_refused"
    assert check.status.value == "block"
    payload = json.loads(store.get(report.run_id, "checks/check-bi.contract.json"))
    assert payload["compiled"] == []
    refusal = payload["refused"][0]["refusal"]
    assert "NUL or surrogate" in refusal
    assert "\0" not in refusal
    assert verify_bundle(store, report.run_id) == []
