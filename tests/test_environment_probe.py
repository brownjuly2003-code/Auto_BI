"""What each engine is asked for §8.2 step 7, and how its answers become facts.

The probe is IO, so these tests hand it a `RunQuery` that returns canned rows and assert on
the SQL it asked and the facts it built. The canned rows are not invented: they are what the
stands of the pinned versions actually answered (ClickHouse 24.8.14.39, PostgreSQL 14.24),
including the two answers that decide the shape of the whole module —

* ClickHouse's `users.xml` role holds `GRANT INSERT … ON *.*` while `readonly=1` makes it
  refuse writes, so grants alone would grade a correct read-only role as writable;
* `max(Date)` over an empty ClickHouse table is `1970-01-01`, not NULL.

There is one rule with no exception here: **no probe writes**. A gate that tried an INSERT
to learn whether it could would be a gate that writes to somebody's warehouse.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from auto_bi.verification.environment import EnvironmentQuestions, TableQuestion
from auto_bi.verification.environment_probe import collect

#: A statement that would change something starts with one of these. Matching anywhere in
#: the text would flag `has_table_privilege(…, 'INSERT')`, which only asks a question.
WRITE_STATEMENT = re.compile(
    r"^\s*(insert|update|delete|truncate|alter|drop|create|attach|detach|optimize|rename)\b",
    re.IGNORECASE,
)


class FakeEngine:
    """Answers by matching the query, and remembers everything it was asked."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.asked.append(sql)
        # longest marker wins: `SELECT access_type FROM system.grants WHERE user_name =
        # currentUser()` mentions two of them, and only one of them is what it asks for
        matches = [marker for marker in self.answers if marker in sql]
        return self.answers[max(matches, key=len)] if matches else []


def questions() -> EnvironmentQuestions:
    return EnvironmentQuestions(
        engines=["clickhouse"],
        tables=[
            TableQuestion(
                table="dm.sales_daily",
                event_time_column="date",
                timezones=["Europe/Moscow"],
                metric_refs=["metric://sales/gross_margin_pct@3"],
            )
        ],
    )


def clickhouse_engine(**overrides: list[dict[str, Any]]) -> FakeEngine:
    answers: dict[str, list[dict[str, Any]]] = {
        "currentUser() AS value": [{"value": "gate_ro"}],
        "system.settings": [{"value": "1"}],
        "system.grants": [{"access_type": "SELECT"}, {"access_type": "INSERT"}],
        "system.tables": [{"name": "sales_daily"}],
        "count()": [{"rows_seen": 600, "max_event_time": date(2026, 8, 24)}],
    }
    answers.update(overrides)
    return FakeEngine(answers)


def postgres_engine(**overrides: list[dict[str, Any]]) -> FakeEngine:
    answers: dict[str, list[dict[str, Any]]] = {
        "current_user AS name": [{"name": "gate_ro", "superuser": False}],
        "has_table_privilege('dm.sales_daily', 'INSERT')": [
            {"insert": False, "update": False, "delete": False, "truncate": False}
        ],
        "pg_tables": [{"tablename": "sales_daily"}],
        "'SELECT') AS ok": [{"ok": True}],
        "count(*)": [{"rows_seen": 600, "max_event_time": date(2026, 8, 24)}],
    }
    answers.update(overrides)
    return FakeEngine(answers)


# --- the rule with no exception -----------------------------------------------


def test_no_probe_ever_writes() -> None:
    """Read-only is established from what the engine says, never from trying."""
    for engine, name in ((clickhouse_engine(), "clickhouse"), (postgres_engine(), "greengage")):
        collect(engine, questions(), engine=name, target="stand")

        offenders = [sql for sql in engine.asked if WRITE_STATEMENT.match(sql)]
        assert offenders == [], offenders


# --- ClickHouse ---------------------------------------------------------------


def test_clickhouse_reads_read_only_from_the_profile_not_the_grants() -> None:
    """The stand's `users.xml` role is granted INSERT on `*.*` and still refuses writes:
    `readonly=1` is what decides, and grading by grants would call it writable."""
    engine = clickhouse_engine()

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.name == "gate_ro"
    assert "readonly=1" in facts.role.evidence


def test_a_clickhouse_session_that_is_not_readonly_is_graded_by_its_grants() -> None:
    engine = clickhouse_engine(**{"system.settings": [{"value": "0"}]})

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert "INSERT" in facts.role.evidence


def test_a_clickhouse_session_with_no_write_grant_is_read_only_without_the_profile() -> None:
    engine = clickhouse_engine(
        **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": "SELECT"}]}
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert "no write access type granted" in facts.role.evidence


def test_a_clickhouse_mart_reports_its_rows_beside_its_newest_event() -> None:
    facts = collect(clickhouse_engine(), questions(), engine="clickhouse", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 600)
    assert table.max_event_time == "2026-08-24"


def test_an_empty_clickhouse_mart_reports_no_event_time_at_all() -> None:
    """`max(Date)` over no rows is `1970-01-01` there; passing that on would make an empty
    mart read as fifty years stale instead of unknown."""
    engine = clickhouse_engine(
        **{"count()": [{"rows_seen": 0, "max_event_time": date(1970, 1, 1)}]}
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].rows_seen == 0
    assert facts.tables[0].max_event_time is None


def test_a_clickhouse_table_the_catalog_does_not_hold_is_absent() -> None:
    engine = clickhouse_engine(**{"system.tables": []})

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].exists is False
    assert facts.tables[0].readable is False


def test_a_clickhouse_table_that_answers_nothing_is_unreadable_not_empty() -> None:
    """A role that cannot read the table gets no row back — which is not the same as a
    mart with no rows, and must not be graded as one."""
    engine = clickhouse_engine(**{"count()": []})

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert (facts.tables[0].exists, facts.tables[0].readable) == (True, False)


# --- PostgreSQL family --------------------------------------------------------


def test_postgres_reads_read_only_from_the_privileges() -> None:
    """`transaction_read_only` is `off` for a SELECT-only role on the stand, so the session
    setting says nothing; `has_table_privilege` is the answer."""
    engine = postgres_engine()

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert facts.role.read_only is True
    assert "no INSERT, UPDATE, DELETE or TRUNCATE privilege" in facts.role.evidence
    assert not any("transaction_read_only" in sql for sql in engine.asked)


def test_a_postgres_role_that_may_write_one_in_scope_mart_is_writable() -> None:
    engine = postgres_engine(
        **{
            "has_table_privilege('dm.sales_daily', 'INSERT')": [
                {"insert": True, "update": False, "delete": False, "truncate": False}
            ]
        }
    )

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert "INSERT on dm.sales_daily" in facts.role.evidence


def test_a_postgres_superuser_is_writable_whatever_the_grants_say() -> None:
    """Privileges do not apply to a superuser, so reading them would answer the wrong
    question."""
    engine = postgres_engine(**{"current_user AS name": [{"name": "postgres", "superuser": True}]})

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert "superuser" in facts.role.evidence


def test_a_postgres_null_max_event_time_survives_as_unknown() -> None:
    engine = postgres_engine(**{"count(*)": [{"rows_seen": 0, "max_event_time": None}]})

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert facts.tables[0].max_event_time is None


def test_a_postgres_view_counts_as_a_mart() -> None:
    """A DM served as a view is still the thing the metric reads."""
    engine = postgres_engine(**{"pg_tables": [{"viewname": "sales_daily"}]})

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert facts.tables[0].exists is True


def test_a_postgres_mart_the_role_cannot_select_is_unreadable() -> None:
    engine = postgres_engine(**{"'SELECT') AS ok": [{"ok": False}]})

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert (facts.tables[0].exists, facts.tables[0].readable) == (True, False)


# --- shapes both engines share ------------------------------------------------


def test_a_datetime_reading_keeps_its_offset_as_z() -> None:
    engine = clickhouse_engine(
        **{
            "count()": [
                {
                    "rows_seen": 12,
                    "max_event_time": datetime.fromisoformat("2026-08-25T06:30:00+00:00"),
                }
            ]
        }
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].max_event_time == "2026-08-25T06:30:00Z"


def test_the_facts_carry_where_they_came_from_and_not_how() -> None:
    facts = collect(
        clickhouse_engine(), questions(), engine="clickhouse", target="127.0.0.1:8124/dm"
    )

    assert facts.target == "127.0.0.1:8124/dm"
    assert facts.engine == "clickhouse"
    assert "password" not in facts.model_dump_json()


def test_asking_about_nothing_still_answers_about_the_role() -> None:
    """A product whose metric pins no longer resolve leaves no table to ask about; who the
    gate connected as is still worth recording."""
    engine = clickhouse_engine()

    facts = collect(engine, EnvironmentQuestions(), engine="clickhouse", target="stand")

    assert facts.tables == []
    assert facts.role.name == "gate_ro"
