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

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import (
    CostBound,
    EnvironmentQuestions,
    ExecutionFacts,
    PlanQuestion,
    TableQuestion,
)
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


def questions(columns: dict[str, str] | None = None) -> EnvironmentQuestions:
    return EnvironmentQuestions(
        engines=["clickhouse"],
        tables=[
            TableQuestion(
                table="dm.sales_daily",
                event_time_column="date",
                columns={"date": "Date"} if columns is None else columns,
                timezones=["Europe/Moscow"],
                metric_refs=["metric://sales/gross_margin_pct@3"],
            )
        ],
    )


def clickhouse_engine(**overrides: list[dict[str, Any]]) -> FakeEngine:
    answers: dict[str, list[dict[str, Any]]] = {
        "currentUser() AS value": [{"value": "gate_ro"}],
        "system.settings": [{"value": "1"}],
        "system.grants": [
            {"access_type": "SELECT", "database": None, "table": None, "column": None},
            {"access_type": "INSERT", "database": None, "table": None, "column": None},
        ],
        "system.enabled_roles": [],
        "system.row_policies": [],
        "system.tables": [
            {
                "engine": "MergeTree",
                "sorting_key": "date, store_id",
                "partition_key": "toYYYYMM(date)",
            }
        ],
        "system.columns": [
            {"name": "date", "type": "Date"},
            {"name": "revenue", "type": "Decimal(18, 2)"},
        ],
        "count()": [{"rows_seen": 600, "max_event_time": date(2026, 8, 24)}],
    }
    answers.update(overrides)
    return FakeEngine(answers)


def postgres_engine(**overrides: list[dict[str, Any]]) -> FakeEngine:
    answers: dict[str, list[dict[str, Any]]] = {
        "current_user AS name": [{"name": "gate_ro", "superuser": False, "bypassrls": False}],
        "has_table_privilege('dm.sales_daily', 'INSERT')": [
            {"insert": False, "update": False, "delete": False, "truncate": False}
        ],
        "pg_tables": [{"tablename": "sales_daily"}],
        "format_type": [
            {"name": "date", "type": "date"},
            {"name": "revenue", "type": "numeric(18,2)"},
        ],
        "'SELECT') AS ok": [{"ok": True}],
        "has_any_column_privilege": [{"ok": True}],
        "has_column_privilege(a.attrelid": [{"name": "date", "ok": True}],
        "pg_policy": [],
        "relrowsecurity": [{"enabled": False}],
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
    """No privilege on any column of it — the question that separates a mart out of reach
    from one this role holds only column grants on."""
    engine = postgres_engine(**{"has_any_column_privilege": [{"ok": False}]})

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


# --- the physical layer -------------------------------------------------------


def test_clickhouse_reports_its_columns_and_its_layout() -> None:
    """`system.tables` prints a sorting key as one string; the columns it names are the fact."""
    facts = collect(clickhouse_engine(), questions(), engine="clickhouse", target="stand")

    table = facts.tables[0]
    assert table.columns == {"date": "Date", "revenue": "Decimal(18, 2)"}
    assert table.layout is not None
    assert table.layout.table_engine == "MergeTree"
    assert table.layout.sorting_key == ["date", "store_id"]
    assert table.layout.partition_key == "toYYYYMM(date)"


def test_clickhouse_reports_no_distribution_policy_rather_than_an_empty_one() -> None:
    """It has none to report, and `None` is what keeps `check_physical` from comparing."""
    facts = collect(clickhouse_engine(), questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].layout is not None
    assert facts.tables[0].layout.distribution_key is None


def test_postgres_reports_columns_in_the_spelling_the_model_was_written_in() -> None:
    """`format_type` is the same function the Greenplum introspector wrote the model with,
    so the comparison is between two spellings of one thing."""
    facts = collect(postgres_engine(), questions(), engine="greengage", target="stand")

    assert facts.tables[0].columns == {"date": "date", "revenue": "numeric(18,2)"}


def test_postgres_leaves_the_layout_unreported() -> None:
    """The distribution policy and partition levels live in Greenplum-only catalogs that no
    stand has been available to pin; unreported is the honest answer, not empty."""
    facts = collect(postgres_engine(), questions(), engine="greengage", target="stand")

    assert facts.tables[0].layout is None


# --- the security half: what each engine is asked about reach ------------------


class RefusingEngine:
    """Answers like `FakeEngine`, but raises for the queries named as out of reach.

    Both refusals are the stands' own words: ClickHouse `Code: 497 … ACCESS_DENIED`, and a
    psycopg error carrying `sqlstate` 42501. Asking about access is exactly where a read-only
    role runs out of privilege, so this is the normal case, not the exotic one.
    """

    def __init__(self, inner: FakeEngine, *refused: str, sqlstate: str | None = None) -> None:
        self.inner = inner
        self.refused = refused
        self.sqlstate = sqlstate

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        if any(marker in sql for marker in self.refused):
            error = Exception(
                f"Code: 497. DB::Exception: gate_ro: Not enough privileges. "
                f"(ACCESS_DENIED) while running {sql[:40]}"
                if self.sqlstate is None
                else "permission denied for table sales_daily"
            )
            if self.sqlstate is not None:
                error.sqlstate = self.sqlstate  # type: ignore[attr-defined]
            raise error
        return self.inner(sql)

    @property
    def asked(self) -> list[str]:
        return self.inner.asked


def test_clickhouse_reach_is_read_from_the_grants_not_from_the_catalog() -> None:
    """The stand hides what it does not grant: a role granted five of six columns saw five in
    `system.columns`. Reading reach from the catalog would call the sixth one missing."""
    engine = clickhouse_engine(
        **{
            "system.grants": [
                {
                    "access_type": "SELECT",
                    "database": "dm",
                    "table": "sales_daily",
                    "column": column,
                    "is_partial_revoke": 0,
                }
                for column in ("date", "store_id", "revenue")
            ],
            "system.columns": [
                {"name": "date", "type": "Date"},
                {"name": "revenue", "type": "Decimal(18, 2)"},
            ],
        }
    )

    facts = collect(
        engine,
        questions(columns={"date": "Date", "gross_margin": "Decimal(18, 2)"}),
        engine="clickhouse",
        target="stand",
    )

    assert facts.tables[0].column_reads == {"date": True, "gross_margin": False}


def test_a_partial_revoke_takes_a_column_back() -> None:
    """`REVOKE SELECT(gross_margin)` after a table-wide grant is recorded as a row of its own
    with `is_partial_revoke = 1`, and the stand does refuse that column afterwards."""
    engine = clickhouse_engine(
        **{
            "system.grants": [
                {
                    "access_type": "SELECT",
                    "database": "dm",
                    "table": "sales_daily",
                    "column": None,
                    "is_partial_revoke": 0,
                },
                {
                    "access_type": "SELECT",
                    "database": "dm",
                    "table": "sales_daily",
                    "column": "gross_margin",
                    "is_partial_revoke": 1,
                },
            ]
        }
    )

    facts = collect(
        engine,
        questions(columns={"date": "Date", "gross_margin": "Decimal(18, 2)"}),
        engine="clickhouse",
        target="stand",
    )

    assert facts.tables[0].column_reads == {"date": True, "gross_margin": False}


def test_a_grant_through_an_enabled_role_reaches_this_session() -> None:
    """A privilege can arrive through a role as easily as through a user name, so the roles
    are named in the question rather than left to a join the access catalog does not do."""
    engine = clickhouse_engine(
        **{
            "system.enabled_roles": [{"role_name": "analyst_role"}],
            "system.grants": [
                {
                    "access_type": "SELECT",
                    "database": None,
                    "table": None,
                    "column": None,
                    "is_partial_revoke": 0,
                }
            ],
        }
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert "'analyst_role'" in next(sql for sql in engine.asked if "system.grants" in sql)
    assert facts.tables[0].column_reads == {"date": True}


def test_clickhouse_row_policies_name_users_and_roles() -> None:
    """The stand applies a policy only to what it names — a session no policy names read all
    600 rows — and a policy attached to a role the session holds did filter it."""
    engine = clickhouse_engine(
        **{
            "system.enabled_roles": [{"role_name": "analyst_role"}],
            "system.row_policies": [
                {
                    "short_name": "sec_pol",
                    "select_filter": "store_id = 1",
                    "is_restrictive": 0,
                    "apply_to_all": 0,
                    "apply_to_list": ["gate_rw"],
                    "apply_to_except": [],
                },
                {
                    "short_name": "role_pol",
                    "select_filter": "store_id = 2",
                    "is_restrictive": 1,
                    "apply_to_all": 0,
                    "apply_to_list": ["analyst_role"],
                    "apply_to_except": [],
                },
            ],
        }
    )

    policies = (
        collect(engine, questions(), engine="clickhouse", target="stand").tables[0].row_policies
        or []
    )

    assert [(item.name, item.applies_to_session, item.restrictive) for item in policies] == [
        ("sec_pol", False, False),
        ("role_pol", True, True),
    ]
    assert policies[1].expression == "store_id = 2"


def test_a_policy_that_applies_to_all_but_this_session_does_not() -> None:
    engine = clickhouse_engine(
        **{
            "system.row_policies": [
                {
                    "short_name": "everyone",
                    "select_filter": "store_id = 1",
                    "is_restrictive": 0,
                    "apply_to_all": 1,
                    "apply_to_list": [],
                    "apply_to_except": ["gate_ro"],
                }
            ]
        }
    )

    policies = (
        collect(engine, questions(), engine="clickhouse", target="stand").tables[0].row_policies
        or []
    )

    assert [item.applies_to_session for item in policies] == [False]


def test_clickhouse_has_no_row_security_switch_to_report() -> None:
    """`None` is "the engine does not answer this", which is what keeps the deny-all rule —
    a Postgres shape — from ever firing here."""
    facts = collect(clickhouse_engine(), questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].row_security_enabled is None
    assert facts.tables[0].row_policies == []


def test_an_access_catalog_out_of_reach_is_reported_as_unanswered() -> None:
    """A refusal is a fact about the environment; a crashed run is not a verdict."""
    engine = RefusingEngine(clickhouse_engine(), "system.grants", "system.row_policies")

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.tables[0].column_reads is None
    assert facts.tables[0].row_policies is None
    assert facts.tables[0].readable is True


def test_a_session_that_cannot_show_its_grants_is_not_called_read_only() -> None:
    """`readonly` did not stop this session and the engine will not say what it granted:
    the safe reading of an unanswered question is not "read-only" (invariant 3)."""
    engine = RefusingEngine(
        clickhouse_engine(**{"system.settings": [{"value": "0"}]}), "system.grants"
    )

    role = collect(engine, questions(), engine="clickhouse", target="stand").role

    assert role.read_only is False
    assert "out of reach" in role.evidence


def test_a_reading_the_role_is_refused_is_unreadable_not_a_crash() -> None:
    engine = RefusingEngine(clickhouse_engine(), "count()")

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert (facts.tables[0].exists, facts.tables[0].readable) == (True, False)


def test_an_engine_that_fell_over_is_not_an_access_fact() -> None:
    """Only a refusal is an answer. A dropped connection graded as "no policy applies" would
    be the gate inventing evidence."""

    def dead(sql: str) -> list[dict[str, Any]]:
        raise ConnectionError("connection to server was lost")

    with pytest.raises(ConnectionError):
        collect(dead, questions(), engine="clickhouse", target="stand")


def test_postgres_reads_a_mart_it_only_holds_column_grants_on() -> None:
    """`has_table_privilege(…, 'SELECT')` is false for a column-granted role on the stand,
    while that role reads the mart perfectly well; the narrower question would have blocked a
    release over a mart the gate had just read."""
    engine = postgres_engine(
        **{
            "has_table_privilege('dm.sales_daily', 'SELECT')": [{"ok": False}],
            "has_any_column_privilege": [{"ok": True}],
            "has_column_privilege(a.attrelid": [
                {"name": "date", "ok": True},
                {"name": "gross_margin", "ok": False},
            ],
        }
    )

    facts = collect(
        engine,
        questions(columns={"date": "date", "gross_margin": "numeric(18,2)"}),
        engine="greengage",
        target="stand",
    )

    assert facts.tables[0].readable is True
    assert facts.tables[0].column_reads == {"date": True, "gross_margin": False}


def test_postgres_reports_the_row_security_switch_and_its_policies() -> None:
    engine = postgres_engine(
        **{
            "relrowsecurity": [{"enabled": True}],
            "pg_policy": [
                {
                    "name": "p_sales",
                    "permissive": True,
                    "expression": "(store_id = 1)",
                    "applies": True,
                }
            ],
        }
    )

    table = collect(engine, questions(), engine="greengage", target="stand").tables[0]

    assert table.row_security_enabled is True
    assert (table.row_policies or [])[0].applies_to_session is True
    assert (table.row_policies or [])[0].restrictive is False


def test_postgres_asks_only_about_policies_that_govern_reads() -> None:
    """An INSERT policy does not filter a SELECT, and counting one as applying would turn a
    write rule into a review of somebody's numbers."""
    engine = postgres_engine()

    collect(engine, questions(), engine="greengage", target="stand")

    assert any("polcmd::text IN ('r', '*')" in sql for sql in engine.asked)


def test_a_postgres_superuser_is_not_subject_to_row_security() -> None:
    engine = postgres_engine(**{"current_user AS name": [{"name": "postgres", "superuser": True}]})

    role = collect(engine, questions(), engine="greengage", target="stand").role

    assert role.bypasses_row_security is True


def test_a_plain_postgres_role_is_subject_to_it() -> None:
    engine = postgres_engine(
        **{"current_user AS name": [{"name": "gate_ro", "superuser": False, "bypassrls": False}]}
    )

    role = collect(engine, questions(), engine="greengage", target="stand").role

    assert role.bypasses_row_security is False


# --- plan estimates -----------------------------------------------------------

PLAN_SQL = "SELECT date, store_id, sum(revenue) AS m FROM dm.sales_daily GROUP BY date, store_id"


def priced(
    *, dialect: str = "clickhouse", sql: str = PLAN_SQL, **budgets: int
) -> EnvironmentQuestions:
    asked = questions()
    asked.plans = [
        PlanQuestion(
            metric_ref="metric://sales/gross_margin_pct@3",
            table="dm.sales_daily",
            dialect=dialect,
            sql=sql,
            budgets=MetricBudget(**(budgets or {"max_scanned_rows": 1000})),
        )
    ]
    return asked


def test_clickhouse_prices_a_plan_from_explain_estimate() -> None:
    """The stand answers parts, rows and marks — and nothing about bytes."""
    engine = clickhouse_engine(
        **{
            "EXPLAIN ESTIMATE": [
                {"database": "dm", "table": "sales_daily", "parts": 2, "rows": 600, "marks": 2}
            ]
        }
    )

    facts = collect(engine, priced(), engine="clickhouse", target="stand")

    assert facts.plans[0].scanned_rows == 600
    assert "600 row(s) in 2 part(s), 2 mark(s)" in facts.plans[0].evidence


def test_runtime_cost_probe_gets_the_exact_reference_query_and_returns_typed_facts() -> None:
    engine = clickhouse_engine(**{"EXPLAIN ESTIMATE": []})
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref,
            scanned_bytes=48_000,
            elapsed_ms=17,
            checked_bounds=[CostBound.SCANNED_BYTES, CostBound.TIMEOUT],
            evidence="exact SELECT completed",
        )

    facts = collect(
        engine,
        priced(max_scanned_bytes=50_000, timeout_ms=100),
        engine="clickhouse",
        target="stand",
        run_cost=run_cost,
    )

    assert measured == [PLAN_SQL]
    assert facts.executions == [
        ExecutionFacts(
            metric_ref="metric://sales/gross_margin_pct@3",
            scanned_bytes=48_000,
            elapsed_ms=17,
            checked_bounds=[CostBound.SCANNED_BYTES, CostBound.TIMEOUT],
            evidence="exact SELECT completed",
        )
    ]


def test_runtime_cost_probe_is_not_called_for_a_plan_only_budget() -> None:
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        raise AssertionError("a row estimate does not authorize executing the query")

    facts = collect(
        clickhouse_engine(),
        priced(max_scanned_rows=1000),
        engine="clickhouse",
        target="stand",
        run_cost=run_cost,
    )

    assert measured == []
    assert facts.executions == []


def test_an_empty_clickhouse_estimate_is_not_a_reading_of_zero() -> None:
    """The stand answered this for a `count()` served from metadata *and* for a mart on
    `Memory` whose rows the query really did read; calling it zero would pass any budget."""
    engine = clickhouse_engine(**{"EXPLAIN ESTIMATE": []})

    facts = collect(engine, priced(), engine="clickhouse", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert "named no part to read" in facts.plans[0].evidence


def test_a_refused_estimate_is_a_fact_not_a_crash() -> None:
    class Refusing(FakeEngine):
        def __call__(self, sql: str) -> list[dict[str, Any]]:
            if "EXPLAIN" in sql:
                raise RuntimeError("Code: 497. DB::Exception: ACCESS_DENIED")
            return super().__call__(sql)

    engine = Refusing(clickhouse_engine().answers)

    facts = collect(engine, priced(), engine="clickhouse", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert "refused" in facts.plans[0].evidence


def postgres_plan(*nodes: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The one-column document `EXPLAIN (FORMAT JSON)` answers with."""
    return {"EXPLAIN (FORMAT JSON)": [{"QUERY PLAN": list(nodes)}]}


def test_postgres_prices_the_scan_node_and_not_the_root() -> None:
    """The stand's aggregate said 60 above a Seq Scan that said 600: the top of a plan is
    what comes out, and a budget is about what goes in."""
    engine = postgres_engine(
        **postgres_plan(
            {
                "Plan": {
                    "Node Type": "Aggregate",
                    "Plan Rows": 60,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Relation Name": "sales_daily",
                            "Plan Rows": 600,
                        }
                    ],
                }
            }
        )
    )

    facts = collect(engine, priced(dialect="postgres"), engine="greengage", target="stand")

    assert facts.plans[0].scanned_rows == 600
    assert "scanning sales_daily" in facts.plans[0].evidence


def test_a_filtered_postgres_scan_reports_no_estimate_at_all() -> None:
    """WHERE store_id = 1 turned 600 into 30 on the stand: those rows are what survives the
    filter, not what is read."""
    engine = postgres_engine(
        **postgres_plan(
            {
                "Plan": {
                    "Node Type": "Seq Scan",
                    "Relation Name": "sales_daily",
                    "Plan Rows": 30,
                    "Filter": "(store_id = 1)",
                }
            }
        )
    )

    facts = collect(engine, priced(dialect="postgres"), engine="greengage", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert "counts what survives the filter" in facts.plans[0].evidence


def test_a_plan_that_scans_nothing_is_not_a_free_query() -> None:
    """This is what the stand planned for a mart row security had emptied: a one-time false
    filter, no scan node, zero rows."""
    engine = postgres_engine(
        **postgres_plan(
            {
                "Plan": {
                    "Node Type": "Aggregate",
                    "Plan Rows": 1,
                    "Plans": [{"Node Type": "Result", "One-Time Filter": "false", "Plan Rows": 0}],
                }
            }
        )
    )

    facts = collect(engine, priced(dialect="postgres"), engine="greengage", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert "scans no relation at all" in facts.plans[0].evidence


def test_a_postgres_driver_that_hands_back_raw_text_is_read_anyway() -> None:
    document = (
        '[{"Plan": {"Node Type": "Seq Scan", "Relation Name": "sales_daily", "Plan Rows": 600}}]'
    )
    engine = postgres_engine(**{"EXPLAIN (FORMAT JSON)": [{"QUERY PLAN": document}]})

    facts = collect(engine, priced(dialect="postgres"), engine="greengage", target="stand")

    assert facts.plans[0].scanned_rows == 600


def test_a_plan_compiled_for_another_engine_is_not_asked_of_this_one() -> None:
    engine = clickhouse_engine()
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        raise AssertionError("SQL compiled for another engine must not execute")

    facts = collect(
        engine,
        priced(dialect="postgres", max_scanned_bytes=50_000),
        engine="clickhouse",
        target="stand",
        run_cost=run_cost,
    )

    assert facts.plans[0].scanned_rows is None
    assert "compiled for postgres" in facts.plans[0].evidence
    assert not any("EXPLAIN" in sql for sql in engine.asked)
    assert measured == []


def test_a_metric_with_no_reference_query_is_never_asked_about() -> None:
    """Step 6 compiled nothing for it, so there is no plan — and no query to run."""
    engine = clickhouse_engine()
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        raise AssertionError("missing SQL must not execute")

    facts = collect(
        engine,
        priced(sql="", max_scanned_bytes=50_000),
        engine="clickhouse",
        target="stand",
        run_cost=run_cost,
    )

    assert facts.plans == []
    assert facts.executions == []
    assert not any("EXPLAIN" in sql for sql in engine.asked)
    assert measured == []


def test_runtime_query_is_not_run_through_a_writable_role_or_unreadable_mart() -> None:
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        raise AssertionError("unsafe environment must stop before exact execution")

    engines = (
        clickhouse_engine(**{"system.settings": [{"value": "0"}]}),
        clickhouse_engine(**{"count()": []}),
    )
    for engine in engines:
        facts = collect(
            engine,
            priced(max_scanned_bytes=50_000),
            engine="clickhouse",
            target="stand",
            run_cost=run_cost,
        )
        assert facts.executions == []

    assert measured == []


def test_runtime_query_is_not_run_when_row_security_empties_the_mart() -> None:
    measured: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        measured.append(question.sql)
        raise AssertionError("an empty secured reading must not be priced as the full mart")

    engine = postgres_engine(**{"relrowsecurity": [{"enabled": True}], "pg_policy": []})
    facts = collect(
        engine,
        priced(dialect="postgres", max_scanned_bytes=50_000),
        engine="greengage",
        target="stand",
        run_cost=run_cost,
    )

    assert facts.executions == []
    assert measured == []


def test_pricing_a_plan_never_writes_and_never_executes() -> None:
    """EXPLAIN ESTIMATE and EXPLAIN without ANALYZE are plans, not runs."""
    for engine, name, dialect in (
        (clickhouse_engine(), "clickhouse", "clickhouse"),
        (postgres_engine(), "greengage", "postgres"),
    ):
        collect(engine, priced(dialect=dialect), engine=name, target="stand")

        explains = [sql for sql in engine.asked if "EXPLAIN" in sql]
        assert explains, "the plan was never asked for"
        assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
        assert not any("ANALYZE" in sql.upper() for sql in explains)
