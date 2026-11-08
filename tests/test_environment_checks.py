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


def test_columns_the_probe_did_not_ask_about_are_not_findings(tmp_path: Path) -> None:
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
    assert (
        check_physical(
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
        == []
    )


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
