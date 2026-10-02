"""T-29: each environment reading answers its own (mart, event-time column) question."""

from __future__ import annotations

import hashlib
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

from auto_bi.decision.use_cases import VerifyRequest, verify
from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.semantic.model import Aggregation, Column, ColumnRole
from auto_bi.severity import Severity
from auto_bi.verification.environment import (
    CostBound,
    EnvironmentFacts,
    EnvironmentQuestions,
    ExecutionFacts,
    LayoutFacts,
    PlanQuestion,
    RoleFacts,
    RowPolicyFacts,
    TableFacts,
    TableQuestion,
    check_access,
    check_cost,
    check_freshness,
    check_physical,
    check_security,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import (
    CheckStatus,
    _dated,
    _physical_clean,
    _readable,
)
from tests.test_decision_verify import (
    Case,
    _check_payload,
    _write_yaml,
    metric_contract,
    product_contract,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_environment_probe import clickhouse_engine, postgres_engine

MART = "dm.sales_daily"
CLOCKS = ("booked_at", "shipped_at")
FRESH = "2026-10-01T00:00:00Z"
STALE = "2026-09-01T00:00:00Z"
CREATED_AT = "2026-10-01T12:00:00Z"


def questions(*clocks: str, plans: bool = False) -> EnvironmentQuestions:
    tables = [
        TableQuestion(
            table=MART,
            event_time_column=clock,
            columns={clock: "DateTime", clock.replace("_at", "_amount"): "Decimal(18,2)"},
            metric_refs=[f"metric://sales/{clock}@1"],
        )
        for clock in clocks or CLOCKS
    ]
    return EnvironmentQuestions(
        tables=tables,
        plans=(
            [
                PlanQuestion(
                    table=MART,
                    metric_ref=table.metric_refs[0],
                    sql=f'SELECT sum("{table.event_time_column.replace("_at", "_amount")}") '
                    'FROM "dm"."sales_daily"',
                    budgets=MetricBudget(timeout_ms=10),
                )
                for table in tables
            ]
            if plans
            else []
        ),
    )


def reading(clock: str | None, **overrides: Any) -> TableFacts:
    columns = {
        column: kind for question in questions().tables for column, kind in question.columns.items()
    }
    table = TableFacts(
        table=MART,
        exists=True,
        readable=True,
        rows_seen=10,
        max_event_time=FRESH,
        columns=columns,
        column_reads=dict.fromkeys(columns, True),
        row_policies=[],
    )
    # model_copy lets the red tests reach grading before the new field exists.
    return table.model_copy(update={"event_time_column": clock, **overrides})


def facts(*tables: TableFacts, executions: list[ExecutionFacts] | None = None) -> EnvironmentFacts:
    return EnvironmentFacts(
        engine="clickhouse",
        target="offline",
        role=RoleFacts(name="gate_ro", read_only=True, evidence="readonly=1"),
        tables=list(tables),
        executions=executions or [],
    )


def freshness(qs: EnvironmentQuestions, observed: EnvironmentFacts) -> list[Any]:
    return check_freshness(qs, observed, max_age="P1D", created_at=CREATED_AT)


def signature(findings: list[Any]) -> list[tuple[str, Severity, str, str]]:
    return sorted((item.code, item.severity, item.subject, item.detail) for item in findings)


def assert_finding(item: Any, code: str, severity: Severity, subject: str, *detail: str) -> None:
    assert (item.code, item.severity, item.subject) == (code, severity, subject)
    for fragment in detail:
        assert fragment in item.detail


def test_reproducer_is_identical_in_every_reading_and_question_order() -> None:
    booked = reading("booked_at")
    shipped = reading(
        "shipped_at",
        max_event_time=STALE,
        column_reads={"shipped_at": True, "shipped_amount": False},
    )
    seen = []
    for table_order in permutations((booked, shipped)):
        for question_order in permutations(questions().tables):
            qs = EnvironmentQuestions(tables=list(question_order))
            observed = facts(*table_order)
            assert check_access(qs, observed) == []
            assert check_physical(qs, observed) == []
            stale = freshness(qs, observed)
            denied = check_security(qs, observed)
            assert len(stale) == len(denied) == 1
            assert_finding(
                stale[0],
                "freshness.stale",
                Severity.REVIEW,
                MART,
                STALE,
                "by shipped_at",
                "older than the P1D",
            )
            assert_finding(
                denied[0],
                "security.column_unreadable",
                Severity.BLOCK,
                f"{MART}.shipped_amount",
                "shipped_amount",
                "metric://sales/shipped_at@1",
            )
            seen.append(signature(stale + denied))
    assert all(result == seen[0] for result in seen)


@pytest.mark.parametrize("reverse", [False, True])
def test_unreadable_clock_is_not_covered_by_readable_clock(reverse: bool) -> None:
    tables = [reading("booked_at"), reading("shipped_at", readable=False)]
    findings = check_access(questions(), facts(*(tables[::-1] if reverse else tables)))
    assert len(findings) == 1
    assert_finding(
        findings[0],
        "access.table_unreadable",
        Severity.BLOCK,
        MART,
        "gate_ro cannot read dm.sales_daily on offline",
    )


def test_both_fresh_selectable_clocks_are_valid_control() -> None:
    observed = facts(*(reading(clock) for clock in CLOCKS))
    for check in (check_access, freshness, check_physical, check_security):
        assert check(questions(), observed) == []


def test_both_stale_clocks_each_have_their_own_finding() -> None:
    findings = freshness(questions(), facts(*(reading(c, max_event_time=STALE) for c in CLOCKS)))
    assert len(findings) == 2
    for clock, item in zip(CLOCKS, findings, strict=True):
        assert_finding(item, "freshness.stale", Severity.REVIEW, MART, f"by {clock}")


@pytest.mark.parametrize(
    "rows,moment,code",
    [
        (0, None, "freshness.unknown"),
        (10, None, "freshness.unknown"),
        (10, "not-a-timestamp", "freshness.unreadable_event_time"),
    ],
)
@pytest.mark.parametrize("clock", CLOCKS)
def test_unknown_and_unreadable_time_stay_on_their_clock(
    rows: int, moment: str | None, code: str, clock: str
) -> None:
    observed = facts(
        *(
            reading(c, **({"rows_seen": rows, "max_event_time": moment} if c == clock else {}))
            for c in CLOCKS
        )
    )
    findings = freshness(questions(), observed)
    assert len(findings) == 1
    assert_finding(findings[0], code, Severity.REVIEW, MART, clock)


def test_unlabelled_single_clock_reading_preserves_legacy_findings() -> None:
    qs = questions("booked_at")
    legacy = reading(
        None, max_event_time=STALE, column_reads={"booked_at": True, "booked_amount": False}
    )
    for check in (check_access, freshness, check_physical, check_security):
        assert check(qs, facts(legacy)) == check(
            qs, facts(legacy.model_copy(update={"event_time_column": "booked_at"}))
        )
    assert_finding(
        freshness(qs, facts(legacy))[0], "freshness.stale", Severity.REVIEW, MART, "by booked_at"
    )
    assert_finding(
        check_security(qs, facts(legacy))[0],
        "security.column_unreadable",
        Severity.BLOCK,
        f"{MART}.booked_amount",
        "booked_amount",
    )


@pytest.mark.parametrize(
    "clocks,labels,fragments",
    [
        (CLOCKS, (None, None), ("records no column", "several columns", "several readings")),
        (("shipped_at",), ("booked_at",), ("no reading recorded for that column",)),
        (("booked_at",), ("booked_at", "booked_at"), ("2 readings recorded for it",)),
        (("booked_at",), (None, None), ("records no column", "several readings")),
        (CLOCKS, (None,), ("records no column", "several columns")),
        (("booked_at",), ("booked_at", "booked_at", None), ("2 readings recorded for it",)),
    ],
)
def test_unattributed_readings_are_review_and_are_not_graded(
    clocks: tuple[str, ...],
    labels: tuple[str | None, ...],
    fragments: tuple[str, ...],
) -> None:
    qs = questions(*clocks, plans=True)
    observed = facts(
        *(
            reading(
                label,
                max_event_time=STALE,
                columns={},
                column_reads={"booked_amount": False, "shipped_amount": False},
            )
            for label in labels
        )
    )
    findings = check_access(qs, observed)
    assert len(findings) == len(clocks)
    for clock, item in zip(clocks, findings, strict=True):
        assert_finding(
            item, "access.reading_unattributed", Severity.REVIEW, MART, MART, clock, *fragments
        )
    for check in (freshness, check_physical, check_security, check_cost):
        assert check(qs, observed) == []
    assert not _readable(qs, observed)
    assert not _dated(qs, observed)


def test_exact_reading_wins_and_extra_unlabelled_reading_fills_nothing() -> None:
    qs = questions()
    observed = facts(reading("booked_at", max_event_time=STALE), reading(None))
    gaps = check_access(qs, observed)
    assert len(gaps) == 1
    assert_finding(
        gaps[0],
        "access.reading_unattributed",
        Severity.REVIEW,
        MART,
        "shipped_at",
        "records no column",
    )
    findings = freshness(qs, observed)
    assert len(findings) == 1
    assert_finding(findings[0], "freshness.stale", Severity.REVIEW, MART, "by booked_at")


def test_missing_mart_keeps_table_not_probed_control() -> None:
    observed = facts(reading("booked_at").model_copy(update={"table": "dm.other"}))
    findings = check_access(questions("booked_at"), observed)
    assert len(findings) == 1
    assert_finding(
        findings[0],
        "access.table_not_probed",
        Severity.REVIEW,
        MART,
        "is in scope",
        "carries no answer about it",
    )


def test_physical_grades_only_the_answering_reading() -> None:
    observed = facts(reading("booked_at"), reading("shipped_at", columns={"shipped_at": "String"}))
    findings = check_physical(questions(), observed)
    assert len(findings) == 2
    assert_finding(
        findings[0],
        "physical.column_absent",
        Severity.BLOCK,
        f"{MART}.shipped_amount",
        "shipped_amount",
        "metric://sales/shipped_at@1",
    )
    assert_finding(
        findings[1],
        "physical.type_drift",
        Severity.REVIEW,
        f"{MART}.shipped_at",
        "is String",
        "DateTime",
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_clean_wording_uses_only_attributed_layout_readings(reverse: bool) -> None:
    tables = [reading("booked_at"), reading(None, layout=LayoutFacts(table_engine="MergeTree"))]
    observed = facts(*(tables[::-1] if reverse else tables))
    assert _readable(questions(), observed)
    assert _dated(questions(), observed)
    assert "this engine reports no layout to compare" in _physical_clean(questions(), observed)


# Digests pin the complete SELECT replay, including T-33 column-write questions.
REPLAYS = [
    (
        "clickhouse",
        "made",
        {},
        12,
        "c47b9c0d007a49c3d9af4fdd8e08f727b8cfd5a9d4aa1f16c6952a0c6eea3cd7",
    ),
    (
        "clickhouse",
        "absent",
        {"system.tables": []},
        6,
        "4a602a343d06a4930ce00ebcc6304b84145dc25f6cc49b1d461e404e2cb3fda7",
    ),
    (
        "clickhouse",
        "unreadable",
        {"count()": []},
        10,
        "41820a582f2d7f4ea502a39fc5fce29beb4c2b67c28711419d9f26fd1fc99b23",
    ),
    (
        "greengage",
        "made",
        {},
        19,
        "bf86553f5041cd97f05f3e7b7f3cc654d7aaaa336d79ed39de0d8c9a9c754d44",
    ),
    (
        "greengage",
        "absent",
        {"pg_tables": []},
        7,
        "6f94e04172c61e9b372c353210f0fbceb6ae62dc5b6ad8f681e790158404402a",
    ),
    (
        "greengage",
        "no_privilege",
        {"has_any_column_privilege": [{"ok": False}]},
        15,
        "5483a35885ac5f50b3e5fd3d4a4748e7a85c30501be711c75e0bd4056464089c",
    ),
    (
        "greengage",
        "refused",
        {"count(*)": []},
        17,
        "1a2f6a38a0658230b7a32f18fe0e8ba0b26cf65d2f1554d5c6893c3eaca63c37",
    ),
]


@pytest.mark.parametrize("engine,branch,overrides,count,digest", REPLAYS)
def test_native_collectors_label_every_branch_without_changing_queries(
    engine: str,
    branch: str,
    overrides: dict[str, Any],
    count: int,
    digest: str,
) -> None:
    replay = (clickhouse_engine if engine == "clickhouse" else postgres_engine)(**overrides)
    observed = collect(replay, questions(), engine=engine, target="offline")
    assert len(replay.asked) == count
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql in replay.asked)
    assert hashlib.sha256("\n".join(replay.asked).encode()).hexdigest() == digest
    assert [getattr(table, "event_time_column", None) for table in observed.tables] == list(CLOCKS)
    assert [table.exists for table in observed.tables] == [branch != "absent"] * 2
    assert [table.readable for table in observed.tables] == [branch == "made"] * 2
    for table in observed.tables:
        assert table.model_dump(mode="json")["event_time_column"] == table.event_time_column


@pytest.mark.parametrize("engine", ["clickhouse", "greengage"])
@pytest.mark.parametrize("unreadable", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_runtime_probes_and_cost_use_the_metrics_own_clock(
    engine: str,
    unreadable: bool,
    reverse: bool,
) -> None:
    qs = questions(*(CLOCKS[::-1] if reverse else CLOCKS), plans=True)
    count_call = "count()" if engine == "clickhouse" else "count(*)"
    replay = (clickhouse_engine if engine == "clickhouse" else postgres_engine)(
        **{
            f'{count_call} AS rows_seen, max("shipped_at")': (
                [] if unreadable else [{"rows_seen": 10, "max_event_time": FRESH}]
            )
        }
    )
    called = []

    def run_cost(plan: PlanQuestion) -> ExecutionFacts:
        called.append(plan.metric_ref)
        return ExecutionFacts(
            metric_ref=plan.metric_ref,
            elapsed_ms=11,
            checked_bounds=[CostBound.TIMEOUT],
            evidence="offline exact SELECT",
        )

    observed = collect(replay, qs, engine=engine, target="offline", run_cost=run_cost)
    expected = {
        f"metric://sales/{clock}@1" for clock in CLOCKS if clock != "shipped_at" or not unreadable
    }
    assert set(called) == expected
    findings = check_cost(qs, observed)
    assert {item.subject for item in findings} == expected
    assert len(findings) == len(expected)
    for item in findings:
        assert_finding(
            item, "cost.over_budget", Severity.REVIEW, item.subject, "11 ms", "over the 10"
        )


@pytest.mark.parametrize(
    "unusable", ["unreadable", "absent", "row_security", "unattributed", "no_question"]
)
def test_cost_skips_metrics_without_their_own_usable_reading(unusable: str) -> None:
    qs = questions(plans=True)
    shipped = reading("shipped_at")
    if unusable == "unreadable":
        shipped = shipped.model_copy(update={"readable": False})
    elif unusable == "absent":
        shipped = shipped.model_copy(update={"exists": False})
    elif unusable == "row_security":
        shipped = shipped.model_copy(
            update={
                "row_security_enabled": True,
                "row_policies": [RowPolicyFacts(name="other_role", applies_to_session=False)],
            }
        )
    elif unusable == "unattributed":
        shipped = shipped.model_copy(update={"event_time_column": None})
    else:
        qs.tables = qs.tables[:1]
    executions = [
        ExecutionFacts(metric_ref=plan.metric_ref, elapsed_ms=11, evidence="offline")
        for plan in qs.plans
    ]
    observed = facts(reading("booked_at"), shipped, executions=executions)
    findings = check_cost(qs, observed)
    assert len(findings) == 1
    assert_finding(
        findings[0], "cost.over_budget", Severity.REVIEW, "metric://sales/booked_at@1", "11 ms"
    )


def test_gate_and_bundle_grade_both_clocks(tmp_path: Path) -> None:
    case = Case(tmp_path)
    model = semantic_model()
    for clock in CLOCKS:
        model.tables[0].columns.extend(
            [
                Column(name=clock, type="DateTime", role=ColumnRole.TIME),
                Column(
                    name=clock.replace("_at", "_amount"),
                    type="Decimal(18,2)",
                    role=ColumnRole.MEASURE,
                    agg=Aggregation.SUM,
                ),
            ]
        )
    model.dump(case.base / "model.yaml")
    case.with_candidate(model=model)
    product = product_contract()
    product["scope"]["metrics"] = [f"metric://sales/{clock}@1" for clock in CLOCKS]
    product["verification"]["freshness_max_age"] = "P1D"
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    for clock in CLOCKS:
        contract = metric_contract()
        contract["metadata"]["id"] = f"metric://sales/{clock}"
        contract["metadata"]["version"] = 1
        contract["time"]["event_time_column"] = clock
        contract["formula"] = {
            "kind": "typed_aggregate",
            "aggregate": {"table": MART, "column": clock.replace("_at", "_amount"), "agg": "sum"},
        }
        for root in (case.contracts, case.base / "metrics", case.candidate / "metrics"):
            _write_yaml(root / f"{clock}.yaml", contract)

    def probe(qs: EnvironmentQuestions) -> EnvironmentFacts:
        assert {item.event_time_column for item in qs.tables} == set(CLOCKS)
        tables = []
        for item in qs.tables:
            tables.append(
                reading(
                    item.event_time_column,
                    columns=item.columns,
                    column_reads={c: c != "shipped_amount" for c in item.columns},
                    max_event_time=STALE if item.event_time_column == "shipped_at" else FRESH,
                )
            )
        return facts(*tables)

    report = verify(
        VerifyRequest(
            contract_paths=[case.contracts],
            base=case.base,
            candidate=case.candidate,
            evidence_dir=tmp_path / "evidence",
            created_at=CREATED_AT,
            dashboards_dir=case.dashboards,
            probe=probe,
        )
    )
    assert status_of(report, "dwh.freshness") is CheckStatus.REVIEW
    assert summary_of(report, "dwh.freshness") == "freshness.stale"
    assert status_of(report, "dwh.security") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.security") == "security.column_unreadable"
    for check in ("dwh.access", "dwh.freshness", "dwh.physical", "dwh.security", "dwh.cost"):
        payload = _check_payload(tmp_path, report, check)
        assert {table["event_time_column"] for table in payload["facts"]["tables"]} == set(CLOCKS)
    stale = _check_payload(tmp_path, report, "dwh.freshness")["findings"]
    denied = _check_payload(tmp_path, report, "dwh.security")["findings"]
    assert len(stale) == len(denied) == 1
    assert stale[0]["severity"] == "review" and stale[0]["subject"] == MART
    assert "by shipped_at" in stale[0]["detail"]
    assert denied[0]["severity"] == "block"
    assert denied[0]["subject"] == f"{MART}.shipped_amount"
