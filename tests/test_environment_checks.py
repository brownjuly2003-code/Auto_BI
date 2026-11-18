"""§8.2 step 7 — what a running engine is asked, and what its answers mean.

The rules live away from the socket on purpose, so each one can be stated against fabricated
facts. Two of them exist only because a stand said something the documentation does not:

* a ClickHouse `users.xml` user holds `GRANT INSERT … ON *.*` even with `readonly=1`, so
  read-only is a profile question there and a privilege question on Postgres — the fact
  carries the engine's own evidence, never an attempted write;
* `max(Date)` over an empty ClickHouse table is `1970-01-01`, not NULL, so "no rows" has to
  travel beside the reading or an empty mart reads as fifty years stale.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.semantic.model import SemanticModel, Table
from auto_bi.severity import Severity
from auto_bi.verification.environment import (
    CostBound,
    EnvironmentFacts,
    EnvironmentQuestions,
    ExecutionFacts,
    LayoutFacts,
    PlanFacts,
    RoleFacts,
    RowPolicyFacts,
    TableFacts,
    TableQuestion,
    check_access,
    check_cost,
    check_freshness,
    check_physical,
    check_security,
    column_drift,
    denies_all_rows,
    questions_for,
)
from auto_bi.verification.sql_contract import compile_metric_queries
from tests.test_decision_verify import metric_contract, product_contract, semantic_model

CREATED_AT = "2026-08-25T09:00:00Z"


def snapshot_of(
    root: Path, *contracts: dict[str, Any], model: SemanticModel | None = None
) -> SemanticSnapshot:
    root.mkdir(parents=True, exist_ok=True)
    (model or semantic_model()).dump(root / "model.yaml")
    metrics = root / "metrics"
    metrics.mkdir(exist_ok=True)
    for contract in contracts or (metric_contract(),):
        name = contract["metadata"]["id"].rsplit("/", 1)[-1]
        metrics.joinpath(f"{name}.yaml").write_text(
            yaml.safe_dump(contract, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return NativeModelSource(root / "model.yaml", metrics).snapshot()


def product(**verification: Any) -> DecisionProductSpec:
    contract = product_contract()
    contract["verification"].update(verification)
    return DecisionProductSpec.model_validate(contract)


def facts(
    *tables: TableFacts,
    read_only: bool = True,
    evidence: str = "readonly=1",
    plans: list[PlanFacts] | None = None,
    executions: list[ExecutionFacts] | None = None,
) -> EnvironmentFacts:
    return EnvironmentFacts(
        engine="clickhouse",
        target="127.0.0.1:8124/dm",
        role=RoleFacts(name="gate_ro", read_only=read_only, evidence=evidence),
        tables=list(tables),
        plans=plans or [],
        executions=executions or [],
    )


def fact(
    max_event_time: str | None = "2026-08-25", rows: int = 600, **overrides: Any
) -> TableFacts:
    return TableFacts(
        table="dm.sales_daily",
        exists=True,
        readable=True,
        rows_seen=rows,
        max_event_time=max_event_time,
        **overrides,
    )


# --- what gets asked ----------------------------------------------------------


def test_only_the_marts_this_product_reads_are_asked_about(tmp_path: Path) -> None:
    """An environment check is about the numbers behind one decision, not a warehouse audit:
    `dm.stores` is in the model but no in-scope metric reads it at its grain."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert [item.table for item in questions.tables] == ["dm.sales_daily"]
    assert questions.tables[0].event_time_column == "date"
    assert questions.tables[0].metric_refs == ["metric://sales/gross_margin_pct@3"]
    assert questions.engines == ["clickhouse"]


def test_the_declared_timezone_travels_with_the_question(tmp_path: Path) -> None:
    """A date-only event time is read in the timezone the metric declares, so the zone has
    to reach the check."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert questions.tables[0].timezones == ["Europe/Moscow"]


def test_two_engines_leave_no_single_environment_to_probe(tmp_path: Path) -> None:
    model = semantic_model()
    second = model.tables[1].model_dump()
    second["physical"] = {"engine": "greenplum"}
    contract = metric_contract()
    contract["metadata"]["id"] = "metric://sales/store_count"
    contract["formula"] = {
        "kind": "typed_aggregate",
        "aggregate": {"table": "dm.stores", "column": "id", "agg": "count"},
    }
    contract["grain"] = {"table": "dm.stores", "keys": ["id"]}
    contract["time"] = {"event_time_column": "id", "timezone": "UTC"}
    contract["additivity"] = "additive"
    spec = product_contract()
    spec["scope"]["metrics"] = [
        "metric://sales/gross_margin_pct@3",
        "metric://sales/store_count@3",
    ]

    model.tables = [model.tables[0], type(model.tables[1]).model_validate(second)]
    questions = questions_for(
        DecisionProductSpec.model_validate(spec),
        snapshot_of(tmp_path, metric_contract(), contract, model=model),
    )

    assert questions.engines == ["clickhouse", "greenplum"]
    assert questions.single_engine() is None


def test_a_metric_pin_that_no_longer_resolves_asks_nothing(tmp_path: Path) -> None:
    """`decision validate` already grades a dangling pin; this step does not guess a table
    for it."""
    spec = product_contract()
    spec["scope"]["metrics"] = ["metric://sales/gone@1"]

    questions = questions_for(DecisionProductSpec.model_validate(spec), snapshot_of(tmp_path))

    assert questions.tables == []


# --- access -------------------------------------------------------------------


def test_a_read_only_role_reading_its_marts_is_no_finding(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert check_access(questions, facts(fact())) == []


def test_a_role_that_can_write_blocks(tmp_path: Path) -> None:
    """Invariant 3: numbers collected through a role that could have changed the mart are
    not the evidence the bundle claims to hold."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_access(
        questions, facts(fact(), read_only=False, evidence="readonly=0, INSERT granted")
    )

    assert [item.code for item in findings] == ["access.role_writable"]
    assert findings[0].severity is Severity.BLOCK
    assert "readonly=0" in findings[0].detail


def test_a_missing_mart_blocks_and_names_the_metrics(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_access(
        questions, facts(TableFacts(table="dm.sales_daily", exists=False, readable=False))
    )

    assert [item.code for item in findings] == ["access.table_absent"]
    assert findings[0].severity is Severity.BLOCK
    assert "metric://sales/gross_margin_pct@3" in findings[0].detail


def test_a_mart_the_role_cannot_read_blocks(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_access(
        questions, facts(TableFacts(table="dm.sales_daily", exists=True, readable=False))
    )

    assert [item.code for item in findings] == ["access.table_unreadable"]
    assert findings[0].severity is Severity.BLOCK


def test_a_table_the_probe_never_answered_about_is_not_a_pass(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_access(questions, facts())

    assert [item.code for item in findings] == ["access.table_not_probed"]
    assert findings[0].severity is Severity.REVIEW


# --- freshness ----------------------------------------------------------------


def test_a_mart_inside_the_window_is_no_finding(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions,
        facts(fact(max_event_time="2026-08-25T06:30:00Z")),
        max_age="PT6H",
        created_at=CREATED_AT,
    )

    assert findings == []


def test_a_source_older_than_the_declared_window_is_review(tmp_path: Path) -> None:
    """The change is not wrong; whether to ship a decision product on stale data is the
    owner's call — the same grading a stale verified asset already gets."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions,
        facts(fact(max_event_time="2026-08-24T09:00:00Z")),
        max_age="PT6H",
        created_at=CREATED_AT,
    )

    assert [item.code for item in findings] == ["freshness.stale"]
    assert findings[0].severity is Severity.REVIEW
    assert "PT6H" in findings[0].detail


def test_a_date_only_reading_says_what_it_assumed(tmp_path: Path) -> None:
    """A daily mart dates its rows by day; turning that into a moment is an assumption: the
    day's 00:00 in the timezone the metric declares, and the detail names it."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time="2026-08-24")), max_age="PT6H", created_at=CREATED_AT
    )

    assert findings[0].code == "freshness.stale"
    assert "read as 00:00 of that day in Europe/Moscow, 2026-08-23T21:00:00Z" in findings[0].detail


def test_an_empty_mart_is_unknown_freshness_not_a_stale_one(tmp_path: Path) -> None:
    """ClickHouse answers `max(Date)` over no rows with `1970-01-01`. Read alone it would
    make an empty mart fifty years stale; `rows_seen` is what keeps it honest."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions,
        facts(fact(max_event_time="1970-01-01", rows=0)),
        max_age="PT6H",
        created_at=CREATED_AT,
    )

    assert [item.code for item in findings] == ["freshness.unknown"]
    assert findings[0].severity is Severity.REVIEW


def test_a_null_max_event_time_is_unknown_too(tmp_path: Path) -> None:
    """Postgres answers the same query with NULL, and it means the same thing."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time=None, rows=0)), max_age="PT6H", created_at=CREATED_AT
    )

    assert [item.code for item in findings] == ["freshness.unknown"]
    assert findings[0].detail == (
        "dm.sales_daily has no rows to date on 127.0.0.1:8124/dm, so its date says nothing "
        "about how fresh the source is — the check did not run rather than passing"
    )


def test_rows_with_no_newest_event_time_are_unknown_and_say_they_are_there(
    tmp_path: Path,
) -> None:
    """Rows counted and nothing to date them by: a NULL `max()` over rows, or an event-time
    column the engine no longer has (environment_probe.py counts the rows without it). Saying
    "no rows" there would send the reader to look for a load that did happen."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time=None, rows=91)), max_age="PT6H", created_at=CREATED_AT
    )

    assert [item.code for item in findings] == ["freshness.unknown"]
    assert findings[0].severity is Severity.REVIEW
    assert findings[0].detail == (
        "dm.sales_daily has 91 row(s) on 127.0.0.1:8124/dm and no newest date to date them by "
        "— a NULL max, or a column the engine no longer has — the check did not run rather "
        "than passing"
    )


@pytest.mark.parametrize("window", ["P1Y", "P1M", "P1Y6M"])
def test_a_window_without_a_fixed_length_is_not_guessed(tmp_path: Path, window: str) -> None:
    """`P1M` is 28 to 31 days; picking one would be inventing someone else's SLO."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions,
        facts(fact(max_event_time="2020-01-01")),
        max_age=window,
        created_at=CREATED_AT,
    )

    assert [item.code for item in findings] == ["freshness.window_not_comparable"]


@pytest.mark.parametrize(
    ("window", "event_time", "stale"),
    [
        ("PT90M", "2026-08-25T08:00:00Z", False),  # 60 minutes old
        ("PT90M", "2026-08-25T07:00:00Z", True),  # 120 minutes old
        ("P1D", "2026-08-24T10:00:00Z", False),  # 23 hours old
        ("P1W", "2026-08-18T10:00:00Z", False),  # 167 hours old
        ("P1W", "2026-08-17T10:00:00Z", True),  # 191 hours old
        ("P1WT12H", "2026-08-17T22:00:00Z", False),  # 179 hours against a 180-hour window
    ],
)
def test_the_window_is_read_component_by_component(
    tmp_path: Path, window: str, event_time: str, stale: bool
) -> None:
    """`PT90M` is ninety minutes, `P1WT12H` is seven and a half days: the `M` after the `T`
    is minutes, and the one before it would have been months."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time=event_time)), max_age=window, created_at=CREATED_AT
    )

    assert bool(findings) is stale


def test_an_unreadable_event_time_is_named_not_ignored(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time="not a time")), max_age="PT6H", created_at=CREATED_AT
    )

    assert [item.code for item in findings] == ["freshness.unreadable_event_time"]


def test_a_mart_that_is_absent_is_only_an_access_finding(tmp_path: Path) -> None:
    """One problem, one finding: freshness has nothing to say about a table that is not
    there, and saying it twice would weigh one fact twice in the verdict."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    absent = facts(TableFacts(table="dm.sales_daily", exists=False, readable=False))

    assert check_freshness(questions, absent, max_age="PT6H", created_at=CREATED_AT) == []
    assert [item.code for item in check_access(questions, absent)] == ["access.table_absent"]


def test_the_age_is_measured_against_the_runs_own_timestamp(tmp_path: Path) -> None:
    """`created_at` is an input so a verification is reproducible; reading a wall clock here
    would make the verdict depend on when the report was rendered."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    reading = facts(fact(max_event_time="2026-08-25T06:30:00Z"))

    assert check_freshness(questions, reading, max_age="PT6H", created_at=CREATED_AT) == []
    later = check_freshness(questions, reading, max_age="PT6H", created_at="2026-08-25T23:00:00Z")
    assert [item.code for item in later] == ["freshness.stale"]


def test_questions_with_no_tables_ask_nothing_of_either_check() -> None:
    empty = EnvironmentQuestions()

    assert check_access(empty, facts()) == []
    assert check_freshness(empty, facts(), max_age="PT6H", created_at=CREATED_AT) == []


# --- the physical layer -------------------------------------------------------

#: What the ClickHouse stand reports for the fixture mart, column for column.
STAND_COLUMNS = {
    "date": "Date",
    "store_id": "UInt32",
    "region": "String",
    "category": "String",
    "revenue": "Decimal(18, 2)",
    "gross_margin": "Decimal(18, 2)",
}
STAND_LAYOUT = LayoutFacts(
    table_engine="MergeTree",
    sorting_key=["date", "store_id"],
    partition_key="toYYYYMM(date)",
    distribution_key=None,
)


def physical_fact(
    columns: dict[str, str] | None = None, layout: LayoutFacts | None = None
) -> TableFacts:
    return TableFacts(
        table="dm.sales_daily",
        exists=True,
        readable=True,
        rows_seen=600,
        max_event_time="2026-08-25",
        columns=STAND_COLUMNS if columns is None else columns,
        layout=STAND_LAYOUT if layout is None else layout,
    )


def test_only_the_columns_the_number_is_made_of_are_asked_about(tmp_path: Path) -> None:
    """`MetricNode.fields` is formula terms, grain keys and the event time — not every column
    the mart happens to carry."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert questions.tables[0].columns == {
        "date": "Date",
        "store_id": "UInt32",
        "gross_margin": "Decimal(18, 2)",
        "revenue": "Decimal(18, 2)",
    }


def test_the_declared_layout_travels_with_the_question(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    layout = questions.tables[0].layout
    assert layout.table_engine == "MergeTree"
    assert layout.sorting_key == ["date", "store_id"]
    assert layout.partition_key == "toYYYYMM(date)"


def test_a_mart_that_matches_the_model_is_no_finding(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert check_physical(questions, facts(physical_fact())) == []


def test_a_column_the_metric_reads_and_the_mart_lacks_blocks(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    without = {name: type_ for name, type_ in STAND_COLUMNS.items() if name != "gross_margin"}

    findings = check_physical(
        questions,
        facts(
            physical_fact(columns=without).model_copy(
                update={"column_reads": dict.fromkeys(STAND_COLUMNS, True)}
            )
        ),
    )

    assert [item.code for item in findings] == ["physical.column_absent"]
    assert findings[0].severity is Severity.BLOCK
    assert findings[0].subject == "dm.sales_daily.gross_margin"


def test_a_type_that_drifted_asks_for_a_human(tmp_path: Path) -> None:
    """Step 6 compiles an exact ratio *because* the model says Decimal; if the mart holds a
    float, the number the gate reasoned about is not the number the query would return."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    drifted = {**STAND_COLUMNS, "revenue": "Float64"}

    findings = check_physical(questions, facts(physical_fact(columns=drifted)))

    assert [item.code for item in findings] == ["physical.type_drift"]
    assert findings[0].severity is Severity.REVIEW
    assert "Float64" in findings[0].detail


def test_spelling_is_not_drift(tmp_path: Path) -> None:
    """`Decimal(18, 2)` and `decimal(18,2)` are one type; case and spacing are not content."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    respelled = {**STAND_COLUMNS, "revenue": "decimal(18,2)"}

    assert check_physical(questions, facts(physical_fact(columns=respelled))) == []


def test_a_layout_that_drifted_asks_for_a_human(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    moved = LayoutFacts(
        table_engine="MergeTree",
        sorting_key=["store_id", "date"],
        partition_key="toYYYYMM(date)",
    )

    findings = check_physical(questions, facts(physical_fact(layout=moved)))

    assert [item.code for item in findings] == ["physical.layout_drift"]
    assert findings[0].severity is Severity.REVIEW
    assert "sorting key" in findings[0].detail


def test_a_layout_the_engine_does_not_report_is_not_drift(tmp_path: Path) -> None:
    """A PostgreSQL stand has no distribution policy to answer with, and silence must not
    read as a contradiction of the model."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    silent = LayoutFacts(table_engine="MergeTree", sorting_key=["date", "store_id"])

    assert check_physical(questions, facts(physical_fact(layout=silent))) == []
    assert check_physical(questions, facts(physical_fact(layout=None))) == []


def test_what_the_model_does_not_claim_is_not_compared(tmp_path: Path) -> None:
    model = semantic_model()
    fact = model.tables[0].model_dump()
    fact["physical"] = {"engine": "clickhouse"}
    model.tables = [Table.model_validate(fact), *model.tables[1:]]
    questions = questions_for(product(), snapshot_of(tmp_path, model=model))

    assert check_physical(questions, facts(physical_fact())) == []


def test_columns_the_engine_did_not_answer_about_are_unobserved_not_absent(
    tmp_path: Path,
) -> None:
    """An empty catalog is an answer: the declared columns are gone. No catalog is none: they
    are not gone, and not compared either — `physical.not_observed` (ADR 0012, D2)."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert (
        check_physical(
            questions,
            facts(
                physical_fact(columns={}, layout=None).model_copy(
                    update={"column_reads": dict.fromkeys(STAND_COLUMNS, True)}
                )
            ),
        )
        != []
    )
    unobserved = check_physical(
        questions,
        facts(
            TableFacts(
                table="dm.sales_daily",
                exists=True,
                readable=True,
                rows_seen=1,
                max_event_time="2026-08-25",
            )
        ),
    )
    assert [(item.code, item.severity) for item in unobserved] == [
        ("physical.not_observed", Severity.REVIEW)
    ]


def test_a_mart_that_is_absent_says_nothing_about_its_columns(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    absent = facts(TableFacts(table="dm.sales_daily", exists=False, readable=False))

    assert check_physical(questions, absent) == []


# --- the security half --------------------------------------------------------

#: Every column the in-scope metrics are made of, in reach of this role.
STAND_READS = {"date": True, "store_id": True, "revenue": True, "gross_margin": True}


def secured_fact(**overrides: Any) -> TableFacts:
    """The stand's mart as the probe reports it now: the readings and what narrowed them."""
    reported: dict[str, Any] = {
        "table": "dm.sales_daily",
        "exists": True,
        "readable": True,
        "rows_seen": 600,
        "max_event_time": "2026-08-25",
        "columns": STAND_COLUMNS,
        "layout": STAND_LAYOUT,
        "column_reads": dict(STAND_READS),
        "row_policies": [],
    }
    return TableFacts.model_validate({**reported, **overrides})


def policy(
    name: str = "sec_pol",
    *,
    applies: bool = True,
    restrictive: bool = False,
    expression: str = "store_id = 1",
) -> RowPolicyFacts:
    return RowPolicyFacts(
        name=name, applies_to_session=applies, restrictive=restrictive, expression=expression
    )


def sensitive_snapshot(root: Path, *, column: bool = True, metric: bool = False):
    """A model that declares what §10.1 says the engine — not the document — has to enforce."""
    model = semantic_model()
    if column:
        table = model.tables[0].model_dump()
        for item in table["columns"]:
            if item["name"] == "gross_margin":
                item["classification"] = "confidential"
        model.tables = [Table.model_validate(table), *model.tables[1:]]
    contract = metric_contract()
    if metric:
        contract["classification"] = "restricted"
    return snapshot_of(root, contract, model=model)


def test_a_mart_nothing_narrows_is_no_finding(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert check_security(questions, facts(secured_fact())) == []


def test_a_column_out_of_reach_blocks_and_is_never_called_missing(tmp_path: Path) -> None:
    """ClickHouse hides what it does not grant, so the catalog cannot tell the two apart —
    and "the mart lost a column" is a very different conversation from "grant the role one"."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    withheld = secured_fact(
        columns={name: type_ for name, type_ in STAND_COLUMNS.items() if name != "gross_margin"},
        column_reads={**STAND_READS, "gross_margin": False},
    )

    findings = check_security(questions, facts(withheld))

    assert [item.code for item in findings] == ["security.column_unreadable"]
    assert findings[0].severity is Severity.BLOCK
    assert findings[0].subject == "dm.sales_daily.gross_margin"
    assert check_physical(questions, facts(withheld)) == []


def test_a_policy_that_names_this_session_makes_every_reading_partial(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_security(questions, facts(secured_fact(row_policies=[policy()])))

    assert [item.code for item in findings] == ["security.row_filtered"]
    assert findings[0].severity is Severity.REVIEW
    assert "store_id = 1" in findings[0].detail


def test_a_policy_that_names_someone_else_does_not_narrow_these_numbers(tmp_path: Path) -> None:
    """A governed mart is not a finding; a *filtered reading* is."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    other = secured_fact(row_policies=[policy("ro_pol", applies=False)])

    assert check_security(questions, facts(other)) == []


def test_row_security_with_no_permissive_policy_blocks(tmp_path: Path) -> None:
    """On PostgreSQL this mart answers an empty set and no error at all — the one shape where
    a metric is uncomputable and every reading still looks like a successful one."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    denied = secured_fact(
        rows_seen=0,
        max_event_time=None,
        row_security_enabled=True,
        row_policies=[policy("r", restrictive=True)],
    )

    findings = check_security(questions, facts(denied))

    assert [item.code for item in findings] == ["security.row_policy_denies_all"]
    assert findings[0].severity is Severity.BLOCK
    assert denies_all_rows(denied, facts(denied).role) is True


def test_a_mart_row_security_empties_is_not_a_mart_with_nothing_to_date(tmp_path: Path) -> None:
    """One fact, one finding: the zero rows freshness would report are the same zero rows
    the security check blocks on, and grading them twice weighs them twice."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    denied = secured_fact(
        rows_seen=0,
        max_event_time=None,
        row_security_enabled=True,
        row_policies=[policy("r", restrictive=True)],
    )

    assert check_freshness(questions, facts(denied), max_age="PT6H", created_at=CREATED_AT) == []


def test_a_restrictive_policy_on_an_engine_without_the_switch_filters(tmp_path: Path) -> None:
    """ClickHouse has no `relrowsecurity`: a restrictive-only policy narrowed the stand's
    mart to 30 of 600 rows rather than emptying it, so the switch is what decides."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    filtered = secured_fact(row_policies=[policy("sec_restr", restrictive=True)])

    findings = check_security(questions, facts(filtered))

    assert [item.code for item in findings] == ["security.row_filtered"]
    assert denies_all_rows(filtered, facts(filtered).role) is False


def test_a_session_exempt_from_row_security_reports_the_bypass(
    tmp_path: Path,
) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    unfiltered = EnvironmentFacts(
        engine="greengage",
        target="127.0.0.1:55433/dm",
        role=RoleFacts(
            name="postgres",
            read_only=False,
            evidence="a superuser",
            bypasses_row_security=True,
        ),
        tables=[secured_fact(row_security_enabled=True, row_policies=[policy(applies=False)])],
    )

    findings = check_security(questions, unfiltered)

    assert [item.code for item in findings] == ["security.row_security_bypassed"]
    assert findings[0].severity is Severity.REVIEW


def test_an_access_catalog_the_role_cannot_read_is_not_a_pass(tmp_path: Path) -> None:
    """Not being able to see a policy is not evidence that none applies."""
    questions = questions_for(product(), snapshot_of(tmp_path))
    unanswered = secured_fact(column_reads=None, row_policies=None)

    findings = check_security(questions, facts(unanswered))

    assert [item.code for item in findings] == ["security.not_observed"]
    assert findings[0].severity is Severity.REVIEW
    assert "may read" in findings[0].detail and "row policy" in findings[0].detail


def test_a_declared_restriction_the_engine_does_not_enforce_asks_for_a_human(
    tmp_path: Path,
) -> None:
    questions = questions_for(product(), sensitive_snapshot(tmp_path, metric=True))

    findings = check_security(questions, facts(secured_fact()))

    assert [item.code for item in findings] == ["security.unenforced_classification"]
    assert findings[0].severity is Severity.REVIEW
    assert "dm.sales_daily.gross_margin" in findings[0].detail
    assert "metric://sales/gross_margin_pct@3" in findings[0].detail


def test_a_restriction_the_engine_does_enforce_is_no_finding_of_its_own(tmp_path: Path) -> None:
    questions = questions_for(product(), sensitive_snapshot(tmp_path))
    enforced = secured_fact(row_policies=[policy()])

    assert [item.code for item in check_security(questions, facts(enforced))] == [
        "security.row_filtered"
    ]


def test_a_restriction_enforced_by_withholding_the_column_is_no_finding(tmp_path: Path) -> None:
    questions = questions_for(product(), sensitive_snapshot(tmp_path))
    withheld = secured_fact(column_reads={**STAND_READS, "gross_margin": False})

    assert [item.code for item in check_security(questions, facts(withheld))] == [
        "security.column_unreadable"
    ]


def test_an_unreadable_mart_says_nothing_about_what_narrows_it(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    unreadable = facts(TableFacts(table="dm.sales_daily", exists=True, readable=False))

    assert check_security(questions, unreadable) == []


def test_the_declared_sensitivity_travels_with_the_question(tmp_path: Path) -> None:
    """The rule needs the model's own claim: a mart handed over whole is only a finding
    because something declared it restricted in the first place."""
    questions = questions_for(product(), sensitive_snapshot(tmp_path, metric=True))

    assert questions.tables[0].sensitive_columns == ["gross_margin"]
    assert questions.tables[0].sensitive_metric_refs == ["metric://sales/gross_margin_pct@3"]
    assert questions.tables[0].declares_sensitive() is True


def test_internal_is_the_default_and_not_a_declaration(tmp_path: Path) -> None:
    """`internal` is what an unset field means, so treating it as sensitive would make every
    mart in every model a finding."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    assert questions.tables[0].declares_sensitive() is False


# --- cost: the plan against the ceiling the metric declares --------------------

METRIC_REF = "metric://sales/gross_margin_pct@3"
REFERENCE_SQL = "SELECT date, store_id FROM dm.sales_daily GROUP BY date, store_id"


def budgeted(tmp_path: Path, **budgets: int) -> EnvironmentQuestions:
    """Questions for a product whose only metric declares *budgets*."""
    contract = metric_contract()
    contract["budgets"] = budgets
    return questions_for(product(), snapshot_of(tmp_path, contract))


def plan(scanned_rows: int | None, evidence: str = "EXPLAIN ESTIMATE: 600 row(s)") -> PlanFacts:
    return PlanFacts(metric_ref=METRIC_REF, scanned_rows=scanned_rows, evidence=evidence)


def execution(
    *,
    scanned_bytes: int | None = None,
    elapsed_ms: int | None = None,
    checked_bounds: list[CostBound] | None = None,
    exceeded_bound: CostBound | None = None,
    evidence: str = "exact reference SELECT completed under engine-enforced limits",
) -> ExecutionFacts:
    return ExecutionFacts(
        metric_ref=METRIC_REF,
        scanned_bytes=scanned_bytes,
        elapsed_ms=elapsed_ms,
        checked_bounds=checked_bounds or [],
        exceeded_bound=exceeded_bound,
        evidence=evidence,
    )


def test_only_a_metric_that_declares_a_ceiling_gets_its_plan_priced(tmp_path: Path) -> None:
    """An estimate with nothing to compare it to is a query run on somebody's warehouse
    for a number no rule reads."""
    assert questions_for(product(), snapshot_of(tmp_path)).plans == []

    questions = budgeted(tmp_path, max_scanned_rows=1000)
    assert [item.metric_ref for item in questions.plans] == [METRIC_REF]
    assert questions.plans[0].table == "dm.sales_daily"
    assert questions.plans[0].budgets.max_scanned_rows == 1000


def test_the_query_priced_is_step_6s_own_reference_query(tmp_path: Path) -> None:
    """One definition of what the metric's query is, so a plan is never priced against a
    form the bundle does not hold."""
    compiled = compile_metric_queries(snapshot_of(tmp_path), [METRIC_REF]).by_metric()
    questions = budgeted(tmp_path, max_scanned_rows=1000)

    assert questions.plans[0].sql == compiled[METRIC_REF].sql
    assert questions.plans[0].dialect == compiled[METRIC_REF].dialect


def test_a_plan_inside_its_ceiling_says_nothing(tmp_path: Path) -> None:
    findings = check_cost(
        budgeted(tmp_path, max_scanned_rows=1000), facts(fact(), plans=[plan(600)])
    )

    assert findings == []


def test_a_plan_over_its_ceiling_needs_review(tmp_path: Path) -> None:
    """The number is still right; whether to ship a metric that costs more than its owner
    declared is that owner's call, exactly as with a stale source."""
    findings = check_cost(
        budgeted(tmp_path, max_scanned_rows=500), facts(fact(), plans=[plan(600)])
    )

    assert [item.code for item in findings] == ["cost.over_budget"]
    assert findings[0].severity is Severity.REVIEW
    assert findings[0].subject == METRIC_REF
    assert "reads 600 rows, over the 500" in findings[0].detail
    assert "EXPLAIN ESTIMATE: 600 row(s)" in findings[0].detail


def test_an_estimate_the_engine_did_not_give_is_not_a_pass(tmp_path: Path) -> None:
    """ClickHouse answers an empty estimate for a mart with no parts to report, and that
    is the one reading that must never become a clean number."""
    silent = plan(None, evidence="EXPLAIN ESTIMATE named no part to read")
    findings = check_cost(budgeted(tmp_path, max_scanned_rows=500), facts(fact(), plans=[silent]))

    assert [item.code for item in findings] == ["cost.not_estimated"]
    assert findings[0].severity is Severity.REVIEW
    assert "named no part to read" in findings[0].detail
    assert "the check did not run rather than passing" in findings[0].detail


def test_a_ceiling_the_probe_never_answered_for_is_not_a_pass_either(tmp_path: Path) -> None:
    findings = check_cost(budgeted(tmp_path, max_scanned_rows=500), facts(fact()))

    assert [item.code for item in findings] == ["cost.not_estimated"]
    assert "the probe did not answer for it" in findings[0].detail


def test_runtime_bounds_inside_their_ceilings_say_nothing(tmp_path: Path) -> None:
    questions = budgeted(tmp_path, max_scanned_bytes=1_000_000, timeout_ms=30_000)
    measured = execution(
        scanned_bytes=600_000,
        elapsed_ms=120,
        checked_bounds=[CostBound.SCANNED_BYTES, CostBound.TIMEOUT],
    )

    assert check_cost(questions, facts(fact(), executions=[measured])) == []


def test_runtime_bounds_over_their_ceilings_need_review(tmp_path: Path) -> None:
    questions = budgeted(tmp_path, max_scanned_bytes=500_000, timeout_ms=100)
    measured = execution(
        scanned_bytes=600_000,
        elapsed_ms=120,
        checked_bounds=[CostBound.SCANNED_BYTES, CostBound.TIMEOUT],
    )
    findings = check_cost(questions, facts(fact(), executions=[measured]))

    assert [item.code for item in findings] == ["cost.over_budget"] * 2
    assert all(item.severity is Severity.REVIEW for item in findings)
    assert "600000 scanned bytes, over the 500000" in findings[0].detail
    assert "120 ms, over the 100" in findings[1].detail


def test_an_engine_enforced_timeout_is_over_budget_without_a_fake_elapsed_time(
    tmp_path: Path,
) -> None:
    questions = budgeted(tmp_path, timeout_ms=30_000)
    timed_out = execution(
        checked_bounds=[CostBound.TIMEOUT],
        exceeded_bound=CostBound.TIMEOUT,
        evidence="TIMEOUT_EXCEEDED at max_execution_time=30s",
    )
    findings = check_cost(questions, facts(fact(), executions=[timed_out]))

    assert [item.code for item in findings] == ["cost.over_budget"]
    assert findings[0].severity is Severity.REVIEW
    assert "TIMEOUT_EXCEEDED" in findings[0].detail


def test_runtime_bounds_the_probe_did_not_measure_are_not_a_pass(tmp_path: Path) -> None:
    questions = budgeted(tmp_path, max_scanned_bytes=1_000_000, timeout_ms=30_000)
    findings = check_cost(questions, facts(fact()))

    assert [item.code for item in findings] == ["cost.not_measured"] * 2
    assert all(item.severity is Severity.REVIEW for item in findings)
    assert all("the probe did not answer for it" in item.detail for item in findings)


def test_a_ceiling_with_no_reference_query_says_so_without_grading_it_twice(
    tmp_path: Path,
) -> None:
    """Step 6 already named the metric it would not compile; repeating that as review would
    give one boundary two weights in the verdict."""
    contract = metric_contract()
    contract["budgets"] = {
        "max_scanned_rows": 500,
        "max_scanned_bytes": 1_000_000,
        "timeout_ms": 30_000,
    }
    contract["formula"] = {
        "kind": "external_ref",
        "external": {
            "source": "dbt",
            "ref": "metric.sales.gross_margin_pct",
            "source_revision": "9f1c0c2e4b7a5d3f8e6c1a0b2d4f6e8c0a2b4d6f",
        },
    }
    questions = questions_for(product(), snapshot_of(tmp_path, contract))

    assert questions.plans[0].sql == ""
    findings = check_cost(questions, facts(fact()))
    assert [item.code for item in findings] == ["cost.plan_unavailable"]
    assert findings[0].severity is Severity.ADVISORY


def test_a_mart_the_gate_could_not_read_is_not_priced(tmp_path: Path) -> None:
    """`check_access` already blocked on it, and pricing a plan over it would weigh one
    fact twice."""
    questions = budgeted(tmp_path, max_scanned_rows=500)

    absent = TableFacts(table="dm.sales_daily", exists=False, readable=False)
    unreadable = TableFacts(table="dm.sales_daily", exists=True, readable=False)
    assert check_cost(questions, facts(absent)) == []
    assert check_cost(questions, facts(unreadable)) == []


def test_a_mart_row_security_empties_is_not_priced_either(tmp_path: Path) -> None:
    """Its plan scans nothing, which as a budget reading would be a clean pass over a mart
    that answers nothing — the same sentence freshness refuses to print."""
    emptied = fact(row_security_enabled=True, row_policies=[])
    questions = budgeted(tmp_path, max_scanned_rows=500)

    assert check_cost(questions, facts(emptied, plans=[plan(0)])) == []


def test_column_drift_is_what_physical_grades() -> None:
    """environment_probe.py weighs a `43`/`47` against this reading, and `dwh.physical` grades
    the same one: a column gone (`None`) or held as another type — not one hidden from the role,
    which ClickHouse leaves out of `system.columns`, nor one whose reach ClickHouse did not
    answer, and not a type spelt with other spacing or case."""
    question = TableQuestion(
        table="dm.sales_daily",
        event_time_column="date",
        columns={
            "date": "Date",
            "revenue": "Decimal(18,2)",
            "region": "String",
            "store_id": "UInt32",
            "gross_margin": "Decimal(18,2)",
        },
        timezones=["Europe/Moscow"],
        metric_refs=["metric://sales/gross_margin_pct@3"],
    )
    table = TableFacts(
        table="dm.sales_daily",
        exists=True,
        readable=True,
        columns={"date": "Date", "revenue": "decimal(18, 2)", "store_id": "UInt64"},
        column_reads={"gross_margin": False, "region": True},
    )
    unasked = table.model_copy(update={"column_reads": None})

    assert column_drift(question, table, engine="clickhouse") == {
        "region": None,
        "store_id": "UInt64",
    }
    assert column_drift(question, unasked, engine="clickhouse") == {"store_id": "UInt64"}
    assert column_drift(question, unasked, engine="postgres") == {
        "gross_margin": None,
        "region": None,
        "store_id": "UInt64",
    }
    no_catalog = table.model_copy(update={"columns": None})
    assert column_drift(question, no_catalog, engine="clickhouse") == {}


# --- one PostgreSQL type, any of its names (ADR 0010, F2; ADR 0012, D1) ---------

#: A spelling a model may declare -> the name `format_type` prints for the same type, as the
#: live test creates it on PostgreSQL 16 and the 9.4.26 kernel.
PG_SAME_TYPE = [
    ("float8", "double precision"),
    ("FLOAT8", "double precision"),
    ("Double  Precision", "double precision"),
    ("float", "double precision"),
    ("float(25)", "double precision"),
    ("float(53)", "double precision"),
    ("float4", "real"),
    ("float(1)", "real"),
    ("float(24)", "real"),
    ("float(024)", "real"),
    ("int", "integer"),
    ("int4", "integer"),
    ("int2", "smallint"),
    ("int8", "bigint"),
    ("bool", "boolean"),
    ("decimal(18, 2)", "numeric(18,2)"),
    ("Decimal(18, 2)", "numeric(18,2)"),
    ("dec(18,2)", "numeric(18,2)"),
    ("decimal", "numeric"),
    ("numeric(10)", "numeric(10,0)"),
    ("varchar(20)", "character varying(20)"),
    ("varchar", "character varying"),
    ("char varying (20)", "character varying(20)"),
    ("char(3)", "character(3)"),
    ("char", "character(1)"),
    ("character", "character(1)"),
    ("varbit(8)", "bit varying(8)"),
    ("timestamp", "timestamp without time zone"),
    ("timestamp(3)", "timestamp(3) without time zone"),
    ("timestamptz", "timestamp with time zone"),
    ("timestamptz(3)", "timestamp(3) with time zone"),
    ("time", "time without time zone"),
    ("timetz", "time with time zone"),
    ("bpchar(3)", "character(3)"),
    ("int4[]", "integer[]"),
    ("varchar(10)[3]", "character varying(10)[]"),
    ("int4[][]", "integer[]"),
    ("int4 [ 3 ] [ ]", "integer[]"),
    # D1: a name quoted or qualified by `pg_catalog` is the catalog's own name for the type
    ("pg_catalog.int4", "integer"),
    ("PG_CATALOG.INT4", "integer"),
    ('"pg_catalog"."int4"', "integer"),
    ("pg_catalog . int4", "integer"),
    ('"int4"', "integer"),
    ('"int2"', "smallint"),
    ("pg_catalog.int8", "bigint"),
    ('"varchar"(20)', "character varying(20)"),
    ("pg_catalog.varchar(020)", "character varying(20)"),
    ('"numeric"(10)', "numeric(10,0)"),
    ('"numeric"(18,2)', "numeric(18,2)"),
    ("pg_catalog.numeric(10, 2)", "numeric(10,2)"),
    ('"bpchar"', "bpchar"),
    ("pg_catalog.bpchar(3)", "character(3)"),
    ('"bit"', '"bit"'),
    ("pg_catalog.bit", '"bit"'),
    ('"bit"(3)', "bit(3)"),
    ("pg_catalog.char", '"char"'),
    ('pg_catalog."char"', '"char"'),
    ('"timestamptz"', "timestamp with time zone"),
    ('"time"(3)', "time(3) without time zone"),
    ("pg_catalog.timetz(3)", "time(3) with time zone"),
    ('"int4"[]', "integer[]"),
    ('"int4" array', "integer[]"),
    ("pg_catalog.int4 array", "integer[]"),
    ('"char"[]', '"char"[]'),
    # D1: `bit` with no length is `bit(1)`, as `character` is `character(1)`
    ("bit", "bit(1)"),
    ("bit(01)", "bit(1)"),
    ("bit[]", "bit(1)[]"),
    ("bit array", "bit(1)[]"),
    # D1: the SQL `ARRAY` keyword, with or without its one bound
    ("integer array", "integer[]"),
    ("int4 ARRAY[3]", "integer[]"),
    ("integer array [3]", "integer[]"),
    ("varchar(10) array", "character varying(10)[]"),
    ("double precision array", "double precision[]"),
    ("timestamp with time zone array", "timestamp with time zone[]"),
    # D1: the `national` and `nchar` forms of the character types
    ("national character varying(3)", "character varying(3)"),
    ("national char varying(3)", "character varying(3)"),
    ("nchar varying(3)", "character varying(3)"),
    ("national character(3)", "character(3)"),
    ("nchar(3)", "character(3)"),
    ("nchar", "character(1)"),
    ("national char", "character(1)"),
    ("nchar(3)[]", "character(3)[]"),
    # D1: zeros before a number, and a time precision past six digits, which is cut to six
    ("varchar(020)", "character varying(20)"),
    ("numeric(018,02)", "numeric(18,2)"),
    ("char(03)", "character(3)"),
    ("float(053)", "double precision"),
    ("dec", "numeric"),
    ("dec(10)", "numeric(10,0)"),
    ("timestamp(06)", "timestamp(6) without time zone"),
    ("timestamp(7)", "timestamp(6) without time zone"),
    ("timestamp(100)", "timestamp(6) without time zone"),
    ("timestamptz(7)", "timestamp(6) with time zone"),
    ("timetz(9)", "time(6) with time zone"),
    ('"timestamp"(7)', "timestamp(6) without time zone"),
    ("timestamp(6) array", "timestamp(6) without time zone[]"),
    # D1: a quoted name's modifier; a precision past six digits, up to the grammar's largest
    ('"varbit"(3)', "bit varying(3)"),
    ("pg_catalog.timestamptz(7)", "timestamp(6) with time zone"),
    ("timestamp(2147483647)", "timestamp(6) without time zone"),
]
#: A serial type a model may declare -> the integer type its sequence feeds, as `format_type`
#: prints it. Read on the declared side only: `format_type` prints no serial type, so a catalog
#: that holds `serial4` holds a type of its own — a domain may be called so (PostgreSQL 16 and
#: the 9.4.26 kernel print `serial4` for one).
PG_SERIAL_TYPE = [
    ("serial", "integer"),
    ("SERIAL4", "integer"),
    ('"serial"', "integer"),
    ("bigserial", "bigint"),
    ("serial8", "bigint"),
    ("smallserial", "smallint"),
    ("serial2", "smallint"),
]
#: Two names `format_type` prints for one type: a time type keeps six fractional digits of a
#: second with no precision given, and is the same type as the one given precision 6 (the live
#: test stores one value in both).
PG_SAME_PRINTED = [
    ("timestamp without time zone", "timestamp(6) without time zone"),
    ("timestamp with time zone", "timestamp(6) with time zone"),
    ("time without time zone", "time(6) without time zone"),
    ("time with time zone", "time(6) with time zone"),
    ("timestamp without time zone[]", "timestamp(6) without time zone[]"),
]
#: A spelling of a type other than the one the catalog holds. A name no alias covers, or one
#: that names no type, is compared as written.
PG_OTHER_TYPE = [
    ("float4", "double precision"),
    ("float(24)", "double precision"),
    ("float(25)", "real"),
    ("float(54)", "double precision"),
    ("float(\u0662\u0664)", "real"),
    ("int4", "bigint"),
    ("int8", "integer"),
    ("varchar(20)", "character varying(30)"),
    ("varchar(20)", "character(20)"),
    ("char(2)", "character(1)"),
    ("character", "character varying"),
    ("numeric", "numeric(10,0)"),
    ("numeric(10)", "numeric(10,2)"),
    ("timestamp", "timestamp with time zone"),
    ("timestamptz", "timestamp without time zone"),
    ("time", "timestamp without time zone"),
    ("timestamptz without time zone", "timestamp with time zone"),
    ("integer with time zone", "integer"),
    ("int4[]", "integer"),
    ("int4", "integer[]"),
    ("Float64", "double precision"),
    ("text", "character varying"),
    ('"char"', "character(1)"),
    ("char", '"char"'),
    ("bpchar", "character(1)"),
    ("int4[3 4]", "integer[]"),
    # D1: what the catalog is asked for as written names no type, or another one
    ('"INT4"', "integer"),
    ('"integer"', "integer"),
    ('"decimal"(18,2)', "numeric(18,2)"),
    ('"double precision"', "double precision"),
    ("pg_catalog.integer", "integer"),
    ('"PG_CATALOG".int4', "integer"),
    ("public.int4", "integer"),
    ('"bit"', "bit(1)"),
    ("bit", '"bit"'),
    ("pg_catalog.char(3)", "character(3)"),
    ('"int4"(3)', "integer"),
    ("pg_catalog.interval(3)", "interval(3)"),
    ('"timestamp" with time zone', "timestamp with time zone"),
    # D1: an array of serial, a serial with a modifier or a schema, another serial's width
    ("serial[]", "integer[]"),
    ("smallserial array", "smallint[]"),
    ("serial(3)", "integer"),
    ("pg_catalog.serial", "integer"),
    ("bigserial", "integer"),
    ("smallserial", "integer"),
    ('"serial"[]', "integer"),
    ('"serial"[]', "integer[]"),
    ("serial with time zone", "integer"),
    ("timestamp(7,2)", "timestamp without time zone"),
    # D1: a quoted name is the catalog's as written, its case and blanks too, and a keyword-only
    # name names no type there
    ('"MYTYPE"', '"MyType"'),
    ('"my type"', '"mytype"'),
    ('"my  type"', '"my type"'),
    ('"a (b)"', '"a(b)"'),
    ('"bigint"', "bigint"),
    ('"int"', "integer"),
    ('"smallint"', "smallint"),
    ('"real"', "real"),
    ('"float"', "double precision"),
    ('"dec"', "numeric"),
    ('"decimal"', "numeric"),
    ('"boolean"', "boolean"),
    ('"character"', "character(1)"),
    ('"nchar"', "character(1)"),
    # D1: a blank inside a name or a number is content
    ("doubleprecision", "double precision"),
    ("big int", "bigint"),
    ("numeric(1 8,2)", "numeric(18,2)"),
    # D1: the `ARRAY` keyword after brackets, twice, or with an empty bound
    ("integer array[]", "integer[]"),
    ("integer array array", "integer[]"),
    ("int4[] array", "integer[]"),
    ("national varchar(3)", "character varying(3)"),
    # D1: a precision under six digits, or one the grammar does not read as a number
    ("timestamp(5)", "timestamp without time zone"),
    ("timestamp(2147483648)", "timestamp(6) without time zone"),
]


def held_as(declared: str, held: str) -> tuple[TableQuestion, TableFacts]:
    """A mart whose `revenue` the model declares as `declared` and the engine holds as `held`."""
    question = TableQuestion(
        table="dm.sales_daily",
        event_time_column="date",
        columns={"revenue": declared},
        timezones=["Europe/Moscow"],
        metric_refs=["metric://sales/gross_margin_pct@3"],
    )
    table = TableFacts(
        table="dm.sales_daily", exists=True, readable=True, columns={"revenue": held}
    )
    return question, table


@pytest.mark.parametrize("engine", ["postgres", "greenplum", "greengage"])
@pytest.mark.parametrize(("declared", "held"), PG_SAME_TYPE)
def test_another_name_of_the_postgres_type_the_engine_holds_is_not_drift(
    declared: str, held: str, engine: str
) -> None:
    """`format_type` prints one name per type and a model may write any the engine takes;
    which side writes which name does not matter."""
    assert column_drift(*held_as(declared, held), engine=engine) == {}
    assert column_drift(*held_as(held, declared), engine=engine) == {}


@pytest.mark.parametrize(("declared", "held"), PG_SERIAL_TYPE)
def test_a_declared_serial_is_the_integer_its_sequence_feeds(declared: str, held: str) -> None:
    """A model's `serial` is the `integer` the catalog prints for the column; a catalog that
    prints `serial` holds a type of that name, which a declared `integer` is not."""
    assert column_drift(*held_as(declared, held), engine="postgres") == {}
    assert column_drift(*held_as(held, declared), engine="greenplum") == {"revenue": declared}


def test_a_catalog_s_serial4_is_not_a_declared_integer() -> None:
    """`CREATE DOMAIN public.serial4 AS text`: `format_type` prints `serial4` for a column of
    it, and the column holds text: not a declared `integer`, nor a declared `serial4`, which
    is one."""
    assert column_drift(*held_as("integer", "serial4"), engine="postgres") == {"revenue": "serial4"}
    assert column_drift(*held_as("serial4", "serial4"), engine="postgres") == {"revenue": "serial4"}


@pytest.mark.parametrize(("declared", "held"), PG_SAME_PRINTED)
def test_a_time_type_of_six_digits_is_the_one_of_no_precision(declared: str, held: str) -> None:
    assert column_drift(*held_as(declared, held), engine="postgres") == {}
    assert column_drift(*held_as(held, declared), engine="greenplum") == {}


@pytest.mark.parametrize(("declared", "held"), PG_OTHER_TYPE)
def test_a_postgres_type_of_another_name_and_another_type_is_drift(
    declared: str, held: str
) -> None:
    assert column_drift(*held_as(declared, held), engine="postgres") == {"revenue": held}


#: Read in a child process: a pattern that could match one text two ways retries every split
#: when the match fails, 2^n tries for n repeats, and that would hang the suite, not fail it.
SPELLINGS_CHILD = """
import json, sys
from auto_bi.verification.environment import TableFacts, TableQuestion, column_drift
engine, held, spellings = json.load(sys.stdin)
for spelling in spellings:
    question = TableQuestion(
        table="dm.t", event_time_column="d", columns={"c": spelling},
        timezones=["UTC"], metric_refs=["metric://m/m@1"],
    )
    table = TableFacts(table="dm.t", exists=True, readable=True, columns={"c": held})
    print(json.dumps(column_drift(question, table, engine=engine)))
"""


def drifts_read_at_once(engine: str, held: str, spellings: list[str]) -> list[dict[str, Any]]:
    """`column_drift` of each spelling against `held`, in a child process stopped after a
    minute: a pattern that hangs fails the test instead of hanging the suite."""
    root = Path(column_drift.__code__.co_filename).resolve().parents[2]
    ended = subprocess.run(
        [sys.executable, "-c", SPELLINGS_CHILD],
        input=json.dumps([engine, held, spellings]),
        env=dict(os.environ, PYTHONPATH=str(root)),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert (ended.returncode, ended.stderr) == (0, "")
    return [json.loads(line) for line in ended.stdout.splitlines()]


def test_a_spelling_of_thousands_of_characters_is_read_at_once() -> None:
    """The type a model declares is free text. Each spelling here fails the pattern at its last
    character after thousands of repeats of a part; `[ ]` once matched two ways, a blank before
    or after its empty bound."""
    spellings = [
        "int4" + "[ ]" * 5000 + "x",
        "int4" + " [ 3 ]" * 5000 + " x",
        "a " * 5000 + "!",
        "a" * 20000 + "!",
        "t" + " with time zone" * 2000 + "!",
        "numeric(" + "1," * 5000 + "x",
        "numeric(1)" + " [ ]" * 5000 + "]",
        "float(" + "9" * 5000 + ")",
        "float(" + "0" * 5000 + "24)",
        # D1: quoted and qualified names, the `ARRAY` keyword, a time precision
        '"' + "a" * 20000 + '"',
        "pg_catalog." + "a" * 20000 + "!",
        "pg_catalog . " * 5000 + "int4",
        '"int4"' + "[ ]" * 5000 + "x",
        '"int4"(' + "1," * 5000 + "x",
        "int4" + " array" * 5000,
        "integer array" + " [ 3 ]" * 5000,
        "timestamp(" + "9" * 5000 + ")",
        "timestamp(" + "0" * 5000 + "6)",
        "serial" + "[]" * 5000,
        # D1: a qualified name the quote after it does not let end
        "pg_catalog." + "a" * 200000 + '"',
    ]

    drifts = drifts_read_at_once("postgres", "integer[]", spellings)
    assert drifts == [{"c": "integer[]"}] * len(spellings)


def test_a_clickhouse_spelling_of_thousands_of_characters_is_read_at_once() -> None:
    """ADR 0012, D1: ClickHouse's types nest, and the reader stops at a depth instead of at
    the interpreter's stack; a number of thousands of digits is not converted."""
    spellings = [
        "Nullable(" * 5000 + "Int64" + ")" * 5000,
        "Array(" * 5000 + "BIGINT",
        "Tuple(" + "Int8, " * 20000 + "Int8)",
        "INT(" + "9" * 5000 + ")",
        "Int16(" + "0" * 5000 + "5)",
        "Decimal(" + "1" * 5000 + ", 2)",
        "DateTime64(" + "3, " * 5000 + "'UTC')",
        "DOUBLE " * 5000 + "PRECISION",
        "a " * 5000 + "!",
        "(" * 5000,
        "'" + "x" * 20000,
    ]

    drifts = drifts_read_at_once("clickhouse", "Int64", spellings)
    assert drifts == [{"c": "Int64"}] * len(spellings)


def test_a_float_precision_of_thousands_of_digits_is_read_by_its_value() -> None:
    """`int()` refuses more than 4300 digits; a precision past 53 is none, however many digits
    it takes, and zeros before 24 still make it 24."""
    too_wide = "float(" + "9" * 5000 + ")"
    padded = "float(" + "0" * 5000 + "24)"

    assert column_drift(*held_as(too_wide, "double precision"), engine="postgres") == {
        "revenue": "double precision"
    }
    assert column_drift(*held_as(padded, "real"), engine="postgres") == {}


def test_clickhouse_takes_none_of_postgres_s_names() -> None:
    """ClickHouse's `BIGINT` is its `Int64`; read by PostgreSQL's aliases it would be the `Int8`
    the engine holds (`int8` is `bigint` there), and a narrowed column would compare equal."""
    assert column_drift(*held_as("BIGINT", "Int8"), engine="clickhouse") == {"revenue": "Int8"}
    assert column_drift(*held_as("int4", "integer"), engine="clickhouse") == {"revenue": "integer"}


# --- one ClickHouse type, any of its names (ADR 0012, D1) ----------------------

#: A spelling a model may declare -> the type `system.columns` prints for it, as the live test
#: creates it on ClickHouse 24.8.
CH_SAME_TYPE = [
    ("BIGINT", "Int64"),
    ("bigint", "Int64"),
    ("INT", "Int32"),
    ("INTEGER", "Int32"),
    ("MEDIUMINT", "Int32"),
    ("SMALLINT", "Int16"),
    ("TINYINT(1)", "Int8"),
    ("INT(11)", "Int32"),
    ("INT()", "Int32"),
    ("INT(11) UNSIGNED", "UInt32"),
    ("INT UNSIGNED(11)", "UInt32"),
    ("BIGINT  UNSIGNED", "UInt64"),
    ("TINYINT(1) UNSIGNED", "UInt8"),
    ("SMALLINT(5) UNSIGNED", "UInt16"),
    ("BIGINT(20) SIGNED", "Int64"),
    ("SIGNED", "Int64"),
    ("UNSIGNED", "UInt64"),
    ("BIT", "UInt64"),
    ("YEAR", "UInt16"),
    ("BYTE(1)", "Int8"),
    ("Int8(3)", "Int8"),
    ("Int128(2)", "Int128"),
    ("Int16(00000000000000000000000005)", "Int16"),
    ("DOUBLE", "Float64"),
    ("DOUBLE PRECISION", "Float64"),
    ("Float64(1, -2)", "Float64"),
    ("FLOAT", "Float32"),
    ("FLOAT(53)", "Float32"),
    ("REAL", "Float32"),
    ("FLOAT()", "Float32"),
    ("TEXT", "String"),
    ("VARCHAR(255)", "String"),
    ("NATIONAL CHARACTER VARYING(3)", "String"),
    ("BLOB", "String"),
    ("String(10)", "String"),
    ("String()", "String"),
    ("BINARY(16)", "FixedString(16)"),
    ("FixedString(016)", "FixedString(16)"),
    ("DECIMAL", "Decimal(10, 0)"),
    ("DEC", "Decimal(10, 0)"),
    ("NUMERIC(20)", "Decimal(20, 0)"),
    ("FIXED(5,2)", "Decimal(5, 2)"),
    ("decimal(18,2)", "Decimal(18, 2)"),
    ("Decimal(018, 02)", "Decimal(18, 2)"),
    ("DECIMAL(76, 76)", "Decimal(76, 76)"),
    ("Decimal64(4)", "Decimal(18, 4)"),
    ("Decimal256(76)", "Decimal(76, 76)"),
    ("BOOL", "Bool"),
    ("BOOLEAN", "Bool"),
    ("TIMESTAMP", "DateTime"),
    ("TIMESTAMP()", "DateTime"),
    ("TIMESTAMP(0)", "DateTime"),
    ("TIMESTAMP(9)", "DateTime64(9)"),
    ("DateTime()", "DateTime"),
    ("DateTime64", "DateTime64(3)"),
    ("DateTime64()", "DateTime64(3)"),
    ("DateTime64(03)", "DateTime64(3)"),
    ("DateTime( 'UTC' )", "DateTime('UTC')"),
    ("DateTime32('UTC')", "DateTime('UTC')"),
    ("TIMESTAMP(0, 'UTC')", "DateTime('UTC')"),
    ("DateTime(3, 'UTC')", "DateTime64(3, 'UTC')"),
    ("DATETIME64(3,'Europe/Moscow')", "DateTime64(3, 'Europe/Moscow')"),
    ("INET4", "IPv4"),
    ("INET6", "IPv6"),
    ("Nullable(BIGINT)", "Nullable(Int64)"),
    ("Nullable(DOUBLE PRECISION)", "Nullable(Float64)"),
    ("Array(Array(INT UNSIGNED))", "Array(Array(UInt32))"),
    ("Array(BINARY(4))", "Array(FixedString(4))"),
    ("Array(Nullable(DECIMAL(10,2)))", "Array(Nullable(Decimal(10, 2)))"),
    ("LowCardinality(TEXT)", "LowCardinality(String)"),
    ("LowCardinality(Nullable(VARCHAR(10)))", "LowCardinality(Nullable(String))"),
    ("Map(TEXT, Nullable(BIGINT))", "Map(String, Nullable(Int64))"),
    # D1: the rest of the aliases of `system.data_type_families`, and arguments the engine fills
    ("SINGLE", "Float32"),
    ("INT1", "Int8"),
    ("SET", "UInt64"),
    ("CHAR(10)", "String"),
    ("BYTEA", "String"),
    ("VARBINARY(16)", "String"),
    ("VARCHAR2(10)", "String"),
    ("TINYINT UNSIGNED", "UInt8"),
    ("INTEGER UNSIGNED", "UInt32"),
    ("INT1 UNSIGNED", "UInt8"),
    ("SMALLINT SIGNED", "Int16"),
    ("INT(11)UNSIGNED", "UInt32"),
    ("Nullable(INT(11) UNSIGNED)", "Nullable(UInt32)"),
    ("DATETIME(6)", "DateTime64(6)"),
    ("Array(TIMESTAMP(3))", "Array(DateTime64(3))"),
    ("Map(String, DateTime64)", "Map(String, DateTime64(3))"),
    ("DateTime64(0,'UTC')", "DateTime64(0, 'UTC')"),
    ("DateTime32()", "DateTime"),
    ("FIXED", "Decimal(10, 0)"),
    ("Decimal256(0)", "Decimal(76, 0)"),
    # D1: a type the rules do not read compares as written, but for blanks next to a mark
    ("Enum8('a'=1, 'b'=2)", "Enum8('a' = 1, 'b' = 2)"),
    ("Tuple(a Int64,b String)", "Tuple(a Int64, b String)"),
]
#: A spelling of a type other than the one the catalog holds, or of none the engine takes.
CH_OTHER_TYPE = [
    ("BIGINT", "Int32"),
    ("INT", "UInt32"),
    ("INT UNSIGNED", "Int32"),
    ("int8", "Int64"),
    ("FLOAT", "Float64"),
    ("DOUBLE", "Float32"),
    ("TEXT", "FixedString(16)"),
    ("BINARY(16)", "String"),
    ("DECIMAL", "Decimal(18, 2)"),
    ("DECIMAL(18)", "Decimal(18, 2)"),
    ("DECIMAL()", "Decimal(10, 0)"),
    ("Decimal64(2)", "Decimal(38, 2)"),
    ("Decimal32(10)", "Decimal(9, 9)"),
    ("TIMESTAMP", "DateTime64(3)"),
    ("TIMESTAMP(3)", "DateTime"),
    ("TIMESTAMP(10)", "DateTime64(9)"),
    ("DateTime64", "DateTime64(6)"),
    ("DateTime('UTC')", "DateTime"),
    ("DateTime64('UTC')", "DateTime64(3, 'UTC')"),
    ("DateTime64(0)", "DateTime"),
    ("Nullable(BIGINT)", "Int64"),
    ("BIGINT", "Nullable(Int64)"),
    ("Bool(1)", "Bool"),
    ("DOUBLE UNSIGNED", "Float64"),
    ("YEAR(4) UNSIGNED", "UInt16"),
    ("INT(11) ZEROFILL", "Int32"),
    ("INT(11) SIGNED UNSIGNED", "Int32"),
    ("DOUBLE(10) PRECISION", "Float64"),
    ("NATIONAL(3) CHAR", "String"),
    ("VARCHAR(99999999999999999999)", "String"),
    ("FixedString(0)", "String"),
    ("Array()", "Array(String)"),
    ("Map(TEXT, BIGINT, INT)", "Map(String, Int64)"),
    # D1: arguments the engine refuses
    ("DECIMAL(2, 3)", "Decimal(2, 3)"),
    ("DateTime32(3)", "DateTime64(3)"),
    ("Decimal32(1, 2)", "Decimal(9, 1)"),
    ("INT(11, 2)", "Int32"),
    ("INT(-1)", "Int32"),
    ("VARCHAR('x')", "String"),
    ("INT('x')", "Int32"),
    ("Array(BIGINT, TEXT)", "Array(Int64)"),
    ("LowCardinality(TEXT, INT)", "LowCardinality(String)"),
    ("Nullable(1)", "Nullable(UInt8)"),
    ("DOUBLE(1,2,3)", "Float64"),
    ("Decimal(18, 2, 1)", "Decimal(18, 2)"),
    ("1", "UInt8"),
    # D1: a blank inside a name is content, and so is a string or a name inside a type
    ("Int 64", "Int64"),
    ("Date Time", "DateTime"),
    ("Nullable(Int 64)", "Nullable(Int64)"),
    ("Enum8('active' = 1, 'inactive' = 2)", "Enum8('Active' = 1, 'Inactive' = 2)"),
    ("Enum8('a  b' = 1)", "Enum8('a b' = 1)"),
    ("Tuple(a Int64)", "Tuple(A Int64)"),
    ("Tuple(a UInt8)", "Tuple(au Int8)"),
    ("Enum8('a = b' = 1)", "Enum8('a=b' = 1)"),
    ("Enum8('a\\' b  c' = 1)", "Enum8('a\\' b c' = 1)"),
]
#: A spelling of the type the catalog holds that the rules do not read: compared as written, it
#: errs towards review. `TIME`, which 24.8 reads as `Int64` and later versions as a type of its
#: own; the families whose arguments are not types or numbers alone (`Tuple`, `Enum`,
#: `AggregateFunction`); a zone left empty and a trailing comma, which the engine drops. The
#: live test creates each as written.
CH_UNREAD = [
    ("TIME", "Int64"),
    ("Tuple(BIGINT, TEXT)", "Tuple(Int64, String)"),
    ("ENUM('a' = 1)", "Enum8('a' = 1)"),
    ("AggregateFunction(sum, BIGINT)", "AggregateFunction(sum, Int64)"),
    ("DateTime('')", "DateTime"),
    ("Decimal(18, 2,)", "Decimal(18, 2)"),
]


@pytest.mark.parametrize(("declared", "held"), CH_SAME_TYPE)
def test_another_name_of_the_clickhouse_type_the_engine_holds_is_not_drift(
    declared: str, held: str
) -> None:
    """`system.columns` prints one name per type and a model may write any the engine takes,
    MySQL's and the SQL standard's among them; which side writes which does not matter."""
    assert column_drift(*held_as(declared, held), engine="clickhouse") == {}
    assert column_drift(*held_as(held, declared), engine="clickhouse") == {}


@pytest.mark.parametrize(("declared", "held"), CH_OTHER_TYPE)
def test_a_clickhouse_type_of_another_name_and_another_type_is_drift(
    declared: str, held: str
) -> None:
    assert column_drift(*held_as(declared, held), engine="clickhouse") == {"revenue": held}


@pytest.mark.parametrize(("declared", "held"), CH_UNREAD)
def test_a_clickhouse_spelling_the_rules_do_not_read_errs_towards_review(
    declared: str, held: str
) -> None:
    assert column_drift(*held_as(declared, held), engine="clickhouse") == {"revenue": held}


def test_a_clickhouse_family_name_is_read_in_any_case() -> None:
    """ClickHouse refuses `int64` for `Int64`, but the comparison has never read case: a
    model's `int64` was `Int64` before ClickHouse's other names were read, and still is."""
    assert column_drift(*held_as("int64", "Int64"), engine="clickhouse") == {}
    assert column_drift(*held_as("nullable(bigint)", "Nullable(Int64)"), engine="clickhouse") == {}


def test_an_engine_the_seam_does_not_know_compares_spacing_and_case_only() -> None:
    """The dialect seam reads an engine it does not know as ClickHouse; the type comparison
    does not guess, and reads neither engine's other names there."""
    assert column_drift(*held_as("BIGINT", "Int64"), engine="duckdb") == {"revenue": "Int64"}
    assert column_drift(*held_as("int4", "integer"), engine="duckdb") == {"revenue": "integer"}
    assert column_drift(*held_as("decimal(18, 2)", "Decimal(18,2)"), engine="duckdb") == {}


def test_physical_on_postgres_grades_the_type_not_its_name(tmp_path: Path) -> None:
    questions = questions_for(product(), snapshot_of(tmp_path))
    questions.tables[0].columns = {
        "date": "date",
        "store_id": "int4",
        "revenue": "decimal(18, 2)",
        "gross_margin": "float8",
    }
    held = {
        "date": "date",
        "store_id": "integer",
        "revenue": "numeric(18,2)",
        "gross_margin": "real",
    }
    on_postgres = facts(physical_fact(columns=held)).model_copy(update={"engine": "postgres"})

    findings = check_physical(questions, on_postgres)

    assert [(item.code, item.subject) for item in findings] == [
        ("physical.type_drift", "dm.sales_daily.gross_margin")
    ]
    assert "is real on" in findings[0].detail
    assert "declares float8" in findings[0].detail
