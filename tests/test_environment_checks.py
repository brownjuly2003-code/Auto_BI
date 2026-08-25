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
from auto_bi.semantic.model import SemanticModel
from auto_bi.severity import Severity
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    RoleFacts,
    TableFacts,
    check_access,
    check_freshness,
    questions_for,
)
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
    *tables: TableFacts, read_only: bool = True, evidence: str = "readonly=1"
) -> EnvironmentFacts:
    return EnvironmentFacts(
        engine="clickhouse",
        target="127.0.0.1:8124/dm",
        role=RoleFacts(name="gate_ro", read_only=read_only, evidence=evidence),
        tables=list(tables),
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
    """A date-only event time is read as a moment; the timezone is what makes that a
    stated assumption instead of a silent one."""
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
    """A daily mart dates its rows by day; turning that into a moment is an assumption, and
    the declared timezone can move a day boundary by up to fourteen hours."""
    questions = questions_for(product(), snapshot_of(tmp_path))

    findings = check_freshness(
        questions, facts(fact(max_event_time="2026-08-24")), max_age="PT6H", created_at=CREATED_AT
    )

    assert findings[0].code == "freshness.stale"
    assert "2026-08-24T00:00:00Z" in findings[0].detail
    assert "Europe/Moscow" in findings[0].detail


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
