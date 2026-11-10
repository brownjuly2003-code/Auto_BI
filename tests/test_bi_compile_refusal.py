"""A delivered dashboard the compile refuses is named by `bi.contract`.

`bi.contract` validates a delivered spec with the build path's validator and then compiles
it with the build path's own builders. Those builders refuse some specs the validator
takes — a shared source dataset whose columns would share an alias, a list where the SQL
generator wants one value, a value the contract cannot address, a name the contract cannot
hold, an engine the target has no source for — and they refuse by raising. What they raised
ended `decision verify` with no report and no bundle
(`docs/specs/bi-contract-validation.md`). The refused dashboard is now listed by the check
with what the compile said of it, and the release blocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from auto_bi.agent.dataset_plan import SourceAliasCollisionError
from auto_bi.cli import main
from auto_bi.evidence.canonical import CanonicalizationError
from auto_bi.evidence.manifest import read_bundle, verify_bundle
from auto_bi.ir.spec import DashboardSpec
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Column, ColumnRole, Join, Physical, SemanticModel, Table
from auto_bi.verification import release_gate
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import (
    CHECK_VERSION,
    VERIFIER_VERSION,
    CheckResult,
    CheckStatus,
    GateReport,
    RunStatus,
)
from tests.test_bi_contract_validation import (
    CHECK_PATH,
    REF,
    SECOND_REF,
    MemoryStore,
    assert_digests,
    run,
)
from tests.test_bi_contract_validation import spec as kpi
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import Case, semantic_model, shown
from tests.test_semantic_gateway import model

MART = "dm.sales_daily"
ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]
STORES = {"table": "dm.stores", "on_left": "dm.sales_daily.store_id", "on_right": "dm.stores.id"}
REF_STORES = {
    "table": "ref.stores",
    "on_left": "dm.sales_daily.store_id",
    "on_right": "ref.stores.id",
}
REVENUE = {"column": "revenue", "agg": "sum"}

COMPILED_NONE = "0 dashboard(s) compiled without touching the BI"
COMPILED_ONE = "1 dashboard(s) compiled without touching the BI"
REFUSES = "; not compiled, the compiler refuses the spec: "
INVALID = "; not compiled, the spec does not validate against the candidate semantic model: "

COLLIDES = "SourceAliasCollisionError: source dataset alias "
NON_FINITE = (
    "CanonicalizationError: $.charts[0].query.filters[0].value: "
    "non-finite numbers have no canonical form"
)
PAST_ANY_DOUBLE = (
    "CanonicalizationError: $.charts[0].query.filters[0].value: "
    "integer exceeds the range of an IEEE-754 double and has no JSON form"
)
NO_SUBJECT = "ValidationError: CompiledArtifact: subject: String should have at least 1 character"


# --- specs and models ---------------------------------------------------------


def chart(chart_id: str = "by_region", **query: Any) -> dict[str, Any]:
    body = {"table": MART, "dimensions": ["region"], "measures": [REVENUE], **query}
    return {"id": chart_id, "title": "Revenue", "viz": "bar", "query": body}


def by_store(
    chart_id: str = "by_store", label: str = "dm.stores.id", join: dict[str, str] | None = None
) -> dict[str, Any]:
    return chart(chart_id, dimensions=[label], joins=[join or STORES])


def windowed(**query: Any) -> dict[str, Any]:
    """A chart that keeps a dataset of its own: its measure is a window over the aggregate."""
    return chart(measures=[{**REVENUE, "transform": "share_of_total"}], **query)


def filtered(value: Any, op: str = "=", **query: Any) -> dict[str, Any]:
    return chart(filters=[{"column": "region", "op": op, "value": value}], **query)


def body(*charts: dict[str, Any], target: str = "superset", **over: Any) -> dict[str, Any]:
    return {"title": "Probe", "target_bi": target, "charts": list(charts) or [chart()], **over}


def dashboard(*charts: dict[str, Any], target: str = "superset", **over: Any) -> DashboardSpec:
    return DashboardSpec.model_validate(body(*charts, target=target, **over))


def with_mart_column(semantic: SemanticModel, name: str) -> SemanticModel:
    semantic.tables[0].columns.append(Column(name=name, type="String", role=ColumnRole.DIMENSION))
    return semantic


def with_revenue_twice(semantic: SemanticModel) -> SemanticModel:
    mart = semantic.tables[0]
    mart.columns.append(next(c for c in mart.columns if c.name == "revenue").model_copy(deep=True))
    return semantic


def with_ref_stores(semantic: SemanticModel) -> SemanticModel:
    semantic.tables.append(
        Table(
            name="ref.stores",
            grain=["id"],
            columns=[Column(name="id", type="UInt32", role=ColumnRole.DIMENSION)],
            physical=Physical(engine="clickhouse"),
        )
    )
    semantic.joins.append(Join(left="dm.sales_daily.store_id", right="ref.stores.id"))
    return semantic


def on_engine(semantic: SemanticModel, engine: str) -> SemanticModel:
    for table in semantic.tables:
        table.physical = Physical(engine=engine)
    return semantic


def _check(report: GateReport) -> CheckResult:
    return next(check for check in report.checks if check.check_id == "bi.contract")


def _payload(report: GateReport, store: MemoryStore) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(store.get(report.run_id, CHECK_PATH))
    return payload


def _bi_payloads(report: GateReport, store: MemoryStore) -> list[str]:
    artifacts = read_bundle(store, report.run_id).artifacts
    return [item.artifact_id for item in artifacts if item.kind.value == "bi_contract"]


# --- what the compile refuses ---------------------------------------------------

#: Every spec here validates against its model: the validator's rules are not what refuses.
REFUSED = [
    pytest.param(
        dashboard(by_store()),
        with_mart_column(model(), "stores_id"),
        COLLIDES
        + "'stores_id' collides between 'stores_id' and joined ref 'dm.stores.id' on "
        + MART,
        id="a mart column named as the alias of a joined label",
    ),
    pytest.param(
        dashboard(),
        with_revenue_twice(model()),
        COLLIDES + "'revenue' collides between 'revenue' and mart column 'revenue' on " + MART,
        id="a mart column declared twice",
    ),
    pytest.param(
        dashboard(by_store(), by_store("by_ref_store", "ref.stores.id", REF_STORES)),
        with_ref_stores(model()),
        COLLIDES
        + "'stores_id' collides between 'dm.stores.id' and joined ref 'ref.stores.id' on "
        + MART,
        id="two joined labels of one alias",
    ),
    pytest.param(
        dashboard(windowed(filters=[{"column": "region", "op": "=", "value": ["a", "b"]}])),
        model(),
        "ValueError: operator '=' expects a scalar, got list: ['a', 'b']",
        id="a list for one value, a chart with a dataset of its own",
    ),
    pytest.param(
        dashboard(filtered([1, 2], ">="), target="datalens"),
        model(),
        "ValueError: operator '>=' expects a scalar, got list: [1, 2]",
        id="a list for one value, DataLens",
    ),
    pytest.param(
        dashboard(filtered(float("inf"))), model(), NON_FINITE, id="an infinite filter value"
    ),
    pytest.param(
        dashboard(filtered(float("-inf")), target="datalens"),
        model(),
        NON_FINITE,
        id="an infinite filter value, DataLens",
    ),
    pytest.param(
        dashboard(windowed(filters=[{"column": "region", "op": "=", "value": float("nan")}])),
        model(),
        NON_FINITE,
        id="a filter value that is no number",
    ),
    pytest.param(
        dashboard(filtered(10**400)), model(), PAST_ANY_DOUBLE, id="an integer past any double"
    ),
    pytest.param(dashboard(title=""), model(), NO_SUBJECT, id="a dashboard with no title"),
    pytest.param(
        dashboard(title="   ", target="datalens"),
        model(),
        NO_SUBJECT,
        id="a dashboard titled in blanks, DataLens",
    ),
    pytest.param(dashboard(chart("")), model(), NO_SUBJECT, id="a chart with no id"),
    pytest.param(
        dashboard(target="datalens"),
        on_engine(model(), "duckdb"),
        "ValueError: no DataLens source_type for engine 'duckdb'",
        id="DataLens over an engine it has no source for",
    ),
]


@pytest.mark.parametrize(("delivered", "candidate", "refusal"), REFUSED)
def test_a_dashboard_the_compile_refuses_is_named_and_blocks(
    delivered: DashboardSpec, candidate: SemanticModel, refusal: str
) -> None:
    assert validate_spec(delivered, candidate) == []

    report, store = run({REF: delivered}, candidate_model=candidate)

    check = _check(report)
    assert check.status is CheckStatus.BLOCK
    assert check.summary_code == "bi.compile_refused"
    assert check.check_version == CHECK_VERSION
    assert check.detail == COMPILED_NONE + REFUSES + REF
    assert _payload(report, store) == {
        "compiled": [],
        "invalid": [],
        "missing": [],
        "refused": [{"dashboard": REF, "refusal": refusal}],
    }
    assert _bi_payloads(report, store) == []
    assert report.verdict is Verdict.BLOCK
    assert report.operational_status is RunStatus.COMPLETED
    assert verify_bundle(store, report.run_id) == []


@pytest.mark.parametrize(("delivered", "candidate", "refusal"), REFUSED)
def test_the_refusal_is_what_the_compile_raised(
    delivered: DashboardSpec, candidate: SemanticModel, refusal: str
) -> None:
    with pytest.raises(ValueError) as raised:
        release_gate._compile(delivered, candidate)

    assert release_gate._compile_refusal(raised.value) == refusal
    assert refusal.startswith(type(raised.value).__name__ + ": ")


def test_the_compile_is_asked_once_of_a_dashboard_it_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = dashboard(filtered(float("inf")))
    asked = []
    original = release_gate._compile

    def spy(spec: DashboardSpec, candidate: SemanticModel) -> Any:
        asked.append(spec)
        return original(spec, candidate)

    monkeypatch.setattr(release_gate, "_compile", spy)
    report, _ = run({REF: delivered})

    assert asked == [delivered]
    assert _check(report).summary_code == "bi.compile_refused"


# --- what the compile takes -----------------------------------------------------

COMPILED = [
    pytest.param(
        dashboard(by_store()), model(), id="a joined label and no mart column of its alias"
    ),
    pytest.param(
        dashboard(by_store(), target="datalens"),
        with_mart_column(model(), "stores_id"),
        id="DataLens, which plans no shared dataset, over that mart column",
    ),
    pytest.param(
        dashboard(windowed()),
        with_revenue_twice(model()),
        id="a chart with a dataset of its own over a mart column declared twice",
    ),
    pytest.param(dashboard(filtered(2**53 + 1)), model(), id="an integer past the safe range"),
    pytest.param(dashboard(filtered(1e308)), model(), id="the largest of floats"),
    pytest.param(
        dashboard({**chart(), "title": ""}), model(), id="a chart with no title and an id"
    ),
    pytest.param(
        dashboard(target="datalens"),
        on_engine(model(), "greenplum"),
        id="DataLens over an engine it has a source for",
    ),
]


@pytest.mark.parametrize(("delivered", "candidate"), COMPILED)
def test_a_dashboard_the_compile_takes_is_compiled_as_before(
    delivered: DashboardSpec, candidate: SemanticModel
) -> None:
    assert validate_spec(delivered, candidate) == []
    expected = release_gate._compile(delivered, candidate).model_dump(mode="json")

    report, store = run({REF: delivered}, candidate_model=candidate)

    check = _check(report)
    assert check.status is not CheckStatus.BLOCK
    assert check.summary_code == "bi.compiled"
    assert check.detail == COMPILED_ONE
    assert _payload(report, store) == {
        "compiled": [{"dashboard": REF, **expected}],
        "invalid": [],
        "missing": [],
        "refused": [],
    }
    assert len(_bi_payloads(report, store)) == len(expected["artifacts"])


def test_the_list_of_refused_dashboards_is_sealed_when_it_is_empty() -> None:
    outcomes = {}
    for dashboards in ({REF: kpi()}, {REF: kpi(column="ghost_revenue")}, {}):
        report, store = run(dashboards)
        assert _payload(report, store)["refused"] == []
        outcomes[_check(report).summary_code] = _check(report).detail

    assert outcomes == {
        "bi.compiled": COMPILED_ONE,
        "bi.spec_invalid": COMPILED_NONE + INVALID + REF,
        "bi.spec_missing": COMPILED_NONE + "; no spec supplied for " + REF,
    }


# --- next to other dashboards ---------------------------------------------------


@pytest.mark.parametrize("target", ["superset", "datalens"])
def test_a_refused_dashboard_leaves_the_one_before_it_compiled(target: str) -> None:
    refused = dashboard(filtered(float("inf")), target=target)

    report, store = run({REF: kpi(target), SECOND_REF: refused}, two=True)

    check = _check(report)
    assert check.status is CheckStatus.BLOCK
    assert check.summary_code == "bi.compile_refused"
    assert check.detail == COMPILED_ONE + REFUSES + SECOND_REF
    payload = _payload(report, store)
    assert [item["dashboard"] for item in payload["compiled"]] == [REF]
    assert payload["refused"] == [{"dashboard": SECOND_REF, "refusal": NON_FINITE}]
    assert_digests(report, store, target)


def test_the_dashboard_after_a_refused_one_is_still_compiled() -> None:
    refused = dashboard(filtered(float("inf")))

    report, store = run({REF: refused, SECOND_REF: kpi()}, two=True)

    payload = _payload(report, store)
    assert [item["dashboard"] for item in payload["compiled"]] == [SECOND_REF]
    assert payload["refused"] == [{"dashboard": REF, "refusal": NON_FINITE}]
    assert _check(report).detail == COMPILED_ONE + REFUSES + REF
    stored = _bi_payloads(report, store)
    assert len(stored) == 3
    assert all(item.startswith("bi-dashboard-weekly-margin-8-") for item in stored)


def test_an_invalid_spec_outranks_a_refused_one() -> None:
    invalid = kpi(column="ghost_revenue")
    refused = dashboard(filtered(float("inf")))

    report, store = run({REF: refused, SECOND_REF: invalid}, two=True)

    check = _check(report)
    assert check.status is CheckStatus.BLOCK
    assert check.summary_code == "bi.spec_invalid"
    assert check.detail == COMPILED_NONE + INVALID + SECOND_REF + REFUSES + REF
    payload = _payload(report, store)
    assert payload["invalid"] == [
        {"dashboard": SECOND_REF, "errors": validate_spec(invalid, model())}
    ]
    assert payload["refused"] == [{"dashboard": REF, "refusal": NON_FINITE}]
    assert payload["compiled"] == []
    assert _bi_payloads(report, store) == []


def test_a_refused_spec_outranks_a_missing_one() -> None:
    refused = dashboard(filtered(float("inf")))

    report, store = run({REF: refused}, two=True)

    check = _check(report)
    assert check.status is CheckStatus.BLOCK
    assert check.summary_code == "bi.compile_refused"
    assert check.detail == COMPILED_NONE + REFUSES + REF + "; no spec supplied for " + SECOND_REF
    payload = _payload(report, store)
    assert payload["missing"] == [SECOND_REF]
    assert payload["refused"] == [{"dashboard": REF, "refusal": NON_FINITE}]


def test_refused_dashboards_stand_in_delivery_order_and_the_evidence_repeats() -> None:
    dashboards = {SECOND_REF: dashboard(title=""), REF: dashboard(filtered(float("inf")))}

    first, first_store = run(dashboards, two=True)
    second, second_store = run(dashboards, two=True)

    for report, store in ((first, first_store), (second, second_store)):
        assert _payload(report, store)["refused"] == [
            {"dashboard": REF, "refusal": NON_FINITE},
            {"dashboard": SECOND_REF, "refusal": NO_SUBJECT},
        ]
        assert _check(report).detail == COMPILED_NONE + REFUSES + REF + ", " + SECOND_REF
    assert first.run_id == second.run_id
    assert first_store.get(first.run_id, CHECK_PATH) == second_store.get(second.run_id, CHECK_PATH)


def test_a_spec_that_does_not_validate_is_not_handed_to_the_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered = dashboard(filtered(float("inf"), dimensions=["ghost_region"]))
    errors = validate_spec(delivered, model())
    assert errors == ["chart 'by_region': unknown dimension column 'ghost_region' in " + MART]
    asked = []

    def spy(spec: DashboardSpec, candidate: SemanticModel) -> Any:
        asked.append(spec)
        raise AssertionError("an invalid spec is not compiled")

    monkeypatch.setattr(release_gate, "_compile", spy)
    report, store = run({REF: delivered})

    assert asked == []
    assert _check(report).summary_code == "bi.spec_invalid"
    payload = _payload(report, store)
    assert payload["invalid"] == [{"dashboard": REF, "errors": errors}]
    assert payload["refused"] == []


# --- what is no refusal ---------------------------------------------------------


@pytest.mark.parametrize(
    "raised",
    [RuntimeError("the compile broke"), KeyError("by_region"), TypeError("not a spec")],
    ids=["a runtime error", "a key error", "a type error"],
)
def test_what_the_compile_raises_and_is_no_refusal_is_not_caught(
    raised: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(spec: DashboardSpec, candidate: SemanticModel) -> Any:
        raise raised

    monkeypatch.setattr(release_gate, "_compile", broken)

    with pytest.raises(type(raised)) as caught:
        run({REF: kpi()})

    assert caught.value is raised


# --- what the compile said, in one line -------------------------------------------


class _Four(BaseModel):
    first: int
    second: int
    third: int
    fourth: int


def _schema_refusal(document: dict[str, Any]) -> ValidationError:
    with pytest.raises(ValidationError) as raised:
        _Four.model_validate(document)
    return raised.value


@pytest.mark.parametrize(
    ("raised", "said"),
    [
        (ValueError("two\n   lines\tand a tab"), "ValueError: two lines and a tab"),
        (ValueError("  padded  "), "ValueError: padded"),
        (ValueError(), "ValueError"),
        (ValueError(" \n\t "), "ValueError"),
        (
            SourceAliasCollisionError("alias 'a' collides"),
            "SourceAliasCollisionError: alias 'a' collides",
        ),
        (CanonicalizationError("$.x: no form"), "CanonicalizationError: $.x: no form"),
    ],
    ids=["line breaks", "padding", "no words", "blank words", "a planner class", "a digest class"],
)
def test_what_was_raised_is_said_in_one_line(raised: ValueError, said: str) -> None:
    assert release_gate._compile_refusal(raised) == said


def test_a_schema_refusal_names_the_model_and_where_and_why() -> None:
    refusal = _schema_refusal({})

    assert release_gate._compile_refusal(refusal) == (
        "ValidationError: _Four: first: Field required; second: Field required; "
        "third: Field required; (+1 more)"
    )


def test_a_schema_refusal_leaves_the_value_out() -> None:
    refusal = _schema_refusal({"first": "SEALED-VALUE", "second": 2, "third": 3, "fourth": 4})

    said = release_gate._compile_refusal(refusal)

    assert said == (
        "ValidationError: _Four: first: Input should be a valid integer, "
        "unable to parse string as an integer"
    )
    assert "SEALED-VALUE" in str(refusal)
    assert "SEALED-VALUE" not in said


# --- the command ------------------------------------------------------------------

#: Shapes that ended the command in an exception: the delivered spec and, where the model is
#: what the compile refuses, the model both refs hold.
ENDED_IN_AN_EXCEPTION: dict[str, Any] = {
    "a mart column named as the alias of a joined label": lambda: (
        body(by_store()),
        with_mart_column(semantic_model(), "stores_id"),
    ),
    "a mart column declared twice": lambda: (body(), with_revenue_twice(semantic_model())),
    "two joined labels of one alias": lambda: (
        body(by_store(), by_store("by_ref_store", "ref.stores.id", REF_STORES)),
        with_ref_stores(semantic_model()),
    ),
    "a list for one value": lambda: (
        body(windowed(filters=[{"column": "region", "op": "=", "value": ["a", "b"]}])),
        None,
    ),
    "an infinite filter value": lambda: (body(filtered(float("inf"))), None),
    "an integer past any double": lambda: (body(filtered(10**400), target="datalens"), None),
    "a dashboard with no title": lambda: (body(title=""), None),
    "DataLens over an engine it has no source for": lambda: (
        body(target="datalens"),
        on_engine(semantic_model(), "duckdb"),
    ),
}


def _case(tmp_path: Path, delivered: dict[str, Any], semantic: SemanticModel | None) -> Case:
    case = Case(tmp_path)
    if semantic is not None:
        semantic.dump(case.base / "model.yaml")
    case.with_candidate(semantic).with_asset().with_results(ROWS)
    (case.dashboards / "weekly_margin@7.yaml").write_text(
        yaml.safe_dump(delivered, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return case


@pytest.mark.parametrize("shape", list(ENDED_IN_AN_EXCEPTION))
def test_decision_verify_exits_by_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], shape: str
) -> None:
    delivered, semantic = ENDED_IN_AN_EXCEPTION[shape]()
    case = _case(tmp_path, delivered, semantic)

    assert main(_verify_argv(case)) == 4

    printed = capsys.readouterr()
    assert printed.err == ""
    lines = printed.out.splitlines()
    assert f"{'block':<8} {'bi.contract':<22} bi.compile_refused" in lines
    assert lines[-1].startswith("verdict: block (")
    (run_dir,) = (case.root / "evidence").iterdir()
    assert (run_dir / "manifest.json").is_file()
    payload = json.loads((run_dir / "checks" / "check-bi.contract.json").read_text("utf-8"))
    assert [item["dashboard"] for item in payload["refused"]] == [REF]
    summary = (run_dir / "reports" / "summary.md").read_text(encoding="utf-8")
    assert f"`bi.compile_refused` — {COMPILED_NONE}{REFUSES}{REF}" in shown(summary)


def test_no_other_check_moves_with_a_refused_dashboard(tmp_path: Path) -> None:
    taken = _case(tmp_path / "taken", body(filtered("Москва")), None).run()
    refused = _case(tmp_path / "refused", body(filtered(float("inf"))), None).run()

    def others(report: GateReport) -> dict[str, tuple[CheckStatus, str]]:
        return {
            check.check_id: (check.status, check.summary_code)
            for check in report.checks
            if check.check_id != "bi.contract"
        }

    assert others(refused) == others(taken)
    assert _check(taken).summary_code == "bi.compiled"
    assert _check(refused).summary_code == "bi.compile_refused"
    assert taken.verdict is not Verdict.BLOCK
    assert refused.verdict is Verdict.BLOCK


def test_the_evidence_versions_moved() -> None:
    assert int(VERIFIER_VERSION) >= 96
    assert int(CHECK_VERSION) >= 71
