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
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification import environment_probe
from auto_bi.verification.environment import (
    CostBound,
    EnvironmentQuestions,
    ExecutionFacts,
    PlanQuestion,
    TableQuestion,
    check_access,
    check_physical,
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
        "has_table_privilege('\"dm\".\"sales_daily\"', 'INSERT')": [
            {
                "insert": False,
                "update": False,
                "delete": False,
                "truncate": False,
                "owner_name": "mart_owner",
                "owns_mart": False,
                "schema_owner_name": "schema_owner",
                "owns_schema": False,
            }
        ],
        "pg_tables": [{"tablename": "sales_daily"}],
        "format_type": [
            {"name": "date", "type": "date"},
            {"name": "revenue", "type": "numeric(18,2)"},
        ],
        "'SELECT') AS ok": [{"ok": True}],
        "has_any_column_privilege": [{"ok": True, "column_insert": False, "column_update": False}],
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


@pytest.mark.parametrize("quoted_name", [" gate_ro ", "   "])
@pytest.mark.parametrize("readonly", ["1", "0"])
def test_clickhouse_preserves_exact_role_name_and_access_grading(
    quoted_name: str, readonly: str
) -> None:
    engine = clickhouse_engine(
        **{
            "currentUser() AS value": [{"value": quoted_name}],
            "system.settings": [{"value": readonly}],
        }
    )
    scope = questions()

    facts = collect(engine, scope, engine="clickhouse", target="stand")

    assert facts.role.name == quoted_name
    assert facts.role.read_only is (readonly == "1")
    findings = check_access(scope, facts)
    assert [item.subject for item in findings] == ([] if readonly == "1" else [quoted_name])


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
            "has_table_privilege('\"dm\".\"sales_daily\"', 'INSERT')": [
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
            "has_table_privilege('\"dm\".\"sales_daily\"', 'SELECT')": [{"ok": False}],
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


class Answering(FakeEngine):
    """Raises `error` for any question containing `marker`, answers the rest as before."""

    def __init__(
        self,
        marker: str,
        error: Exception,
        *,
        base: Callable[..., FakeEngine] = clickhouse_engine,
        **overrides: list[dict[str, Any]],
    ) -> None:
        super().__init__(base(**overrides).answers)
        self.marker = marker
        self.error = error

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        if self.marker in sql:
            self.asked.append(sql)
            raise self.error
        return super().__call__(sql)


#: What ClickHouse 24.8.14.39 said, live, when `revenue` became a `Float64` and when it was
#: dropped (tests/test_decision_live_clickhouse.py); the version and the URL around the code
#: are what a sealed fact must not carry.
ILLEGAL_TYPE = RuntimeError(
    "HTTPDriver for http://127.0.0.1:18125 received ClickHouse error code 43\n Code: 43. "
    "DB::Exception: Illegal type Float64 of 1st argument of function divideDecimal. "
    "(ILLEGAL_TYPE_OF_ARGUMENT) (version 24.8.14.39 (official build))"
)
UNKNOWN_IDENTIFIER = RuntimeError(
    "HTTPDriver for http://127.0.0.1:18125 received ClickHouse error code 47\n Code: 47. "
    "DB::Exception: Unknown expression identifier `revenue` in scope SELECT sum(revenue). "
    "(UNKNOWN_IDENTIFIER) (version 24.8.14.39 (official build))"
)
DECLARED = {"date": "Date", "revenue": "Decimal(18,2)"}
#: The catalog of a mart whose `revenue` was retyped, and of one where it is gone.
RETYPED = [{"name": "date", "type": "Date"}, {"name": "revenue", "type": "Float64"}]
DROPPED = [{"name": "date", "type": "Date"}]


def priced_over(columns: dict[str, str], **budgets: int) -> EnvironmentQuestions:
    asked = priced(**budgets)
    asked.tables[0].columns = columns
    return asked


@pytest.mark.parametrize(
    ("error", "catalog", "said"),
    [
        (ILLEGAL_TYPE, RETYPED, "Code 43 ILLEGAL_TYPE_OF_ARGUMENT"),
        (UNKNOWN_IDENTIFIER, DROPPED, "Code 47 UNKNOWN_IDENTIFIER"),
    ],
)
def test_a_reference_query_the_mart_no_longer_fits_is_an_unpriced_plan(
    error: Exception, catalog: list[dict[str, Any]], said: str
) -> None:
    """A retyped or dropped column makes the engine refuse to plan the reference query. That
    is the drift `dwh.physical` exists to report, so it may not end the run before the
    report; the plan stays unpriced and says why, in the code alone."""
    engine = Answering("EXPLAIN", error, **{"system.columns": catalog})

    facts = collect(engine, priced_over(DECLARED), engine="clickhouse", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert facts.plans[0].evidence == (
        f"EXPLAIN ESTIMATE answered the reference query with {said}, taken to mean the "
        "query no longer fits the mart as the engine has it (revenue drifted from the model)"
    )


def clickhouse_error(code: int, text: str) -> Exception:
    """A driver error that carries the ClickHouse code as an attribute, as clickhouse_connect's
    `DatabaseError` does (from the `X-ClickHouse-Exception-Code` header), with words of its own
    around it."""
    error = RuntimeError(text)
    error.code = code  # type: ignore[attr-defined]
    return error


#: `47` for a dropped column whose name holds a refusal's words: the text matches the
#: refusal wording, the code does not.
ACCESS_DENIED_AT_GONE = RuntimeError(
    "HTTPDriver for http://127.0.0.1:18125 received ClickHouse error code 47\n Code: 47. "
    "DB::Exception: Unknown expression identifier `access_denied_at` in scope SELECT "
    "max(access_denied_at) FROM dm.sales_daily. (UNKNOWN_IDENTIFIER) (version 24.8.14.39 "
    "(official build))"
)


@pytest.mark.parametrize(
    ("error", "refused"),
    [
        (RuntimeError("Code: 497. DB::Exception: Not enough privileges. (ACCESS_DENIED)"), True),
        (RuntimeError("Code: 291. DB::Exception: (DATABASE_ACCESS_DENIED)"), True),
        (RuntimeError("Code: 60. DB::Exception: Unknown table 'dm.x'. (UNKNOWN_TABLE)"), True),
        (RuntimeError("Code: 81. DB::Exception: No database dm. (UNKNOWN_DATABASE)"), True),
        (clickhouse_error(497, "Недостаточно прав"), True),
        (clickhouse_error(60, "server said no"), True),
        (ACCESS_DENIED_AT_GONE, False),
        (clickhouse_error(47, "Missing columns: 'access_denied_at' (UNKNOWN_IDENTIFIER)"), False),
        (RuntimeError("Code: 159. DB::Exception: Timeout in unknown_table_log."), False),
        (RuntimeError("gate_ro: Not enough privileges"), True),
        (RuntimeError("ACCESS_DENIED"), True),
        (RuntimeError("Connection refused"), False),
    ],
    ids=[
        "497",
        "291",
        "60",
        "81",
        "497-attribute-other-words",
        "60-attribute-other-words",
        "47-quoting-access-denied",
        "47-attribute-quoting-access-denied",
        "159-quoting-unknown-table",
        "no-code-text",
        "no-code-name",
        "no-code-other",
    ],
)
def test_a_clickhouse_error_with_a_code_is_a_refusal_by_its_code(
    error: Exception, refused: bool
) -> None:
    """The code decides where the error carries one; the words are read only where it does
    not, as a Postgres error is read by its SQLSTATE before its text."""
    assert environment_probe._refused(error) is refused


def test_a_dropped_column_named_like_a_refusal_is_the_drift_it_is() -> None:
    """`access_denied_at` gone answers `47`, whose text quotes the name. Read by the text it
    was a refused `EXPLAIN ESTIMATE`; read by the code it is the drift `dwh.physical`
    reports, and the plan says so."""
    engine = Answering("EXPLAIN", ACCESS_DENIED_AT_GONE, **{"system.columns": DROPPED})
    asked = priced(sql="SELECT max(access_denied_at) FROM dm.sales_daily")
    asked.tables[0].columns = {"date": "Date", "access_denied_at": "DateTime"}

    facts = collect(engine, asked, engine="clickhouse", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert facts.plans[0].evidence == (
        "EXPLAIN ESTIMATE answered the reference query with Code 47 UNKNOWN_IDENTIFIER, taken "
        "to mean the query no longer fits the mart as the engine has it (access_denied_at "
        "drifted from the model)"
    )


def test_an_event_time_column_named_like_a_refusal_leaves_the_rows_counted_and_undated() -> None:
    """The same `47` for `max()` of the event-time column: read by the text, `max()` was
    refused and the run never counted the rows; read by the code they are counted, undated."""
    engine = Answering(
        "max(",
        ACCESS_DENIED_AT_GONE,
        **{"count() AS rows_seen FROM": [{"rows_seen": 91}], "system.columns": DROPPED},
    )
    asked = questions({"date": "Date", "access_denied_at": "DateTime"})
    asked.tables[0].event_time_column = "access_denied_at"

    facts = collect(engine, asked, engine="clickhouse", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 91)
    assert table.max_event_time is None


@pytest.mark.parametrize("error", [ILLEGAL_TYPE, UNKNOWN_IDENTIFIER])
def test_a_query_the_undrifted_mart_does_not_fit_still_ends_the_run(error: Exception) -> None:
    """The catalog holds every column the metric reads as declared, so the engine's `43`/`47`
    points at the query — the gate's own, or a model declaring a type its aggregate does not
    take. That is a defect to fail on loudly, not a drift to report."""
    engine = Answering("EXPLAIN", error, **{"system.columns": RETYPED})

    with pytest.raises(RuntimeError) as raised:
        collect(
            engine,
            priced_over({"date": "Date", "revenue": "Float64"}),
            engine="clickhouse",
            target="stand",
        )

    assert raised.value is error


@pytest.mark.parametrize(
    ("error", "catalog", "declared"),
    [
        # `47` is a column gone; the only drift here is a retype
        (UNKNOWN_IDENTIFIER, RETYPED, DECLARED),
        # `43` is a column retyped; the only drift here is a column gone
        (ILLEGAL_TYPE, DROPPED, DECLARED),
        # gone, but the reference query does not read `gross_margin`
        (
            UNKNOWN_IDENTIFIER,
            [{"name": "date", "type": "Date"}, {"name": "revenue", "type": "Decimal(18, 2)"}],
            {**DECLARED, "gross_margin": "Decimal(18,2)"},
        ),
    ],
    ids=["47-over-a-retype", "43-over-a-drop", "drift-the-query-does-not-read"],
)
def test_a_drift_that_does_not_account_for_the_code_still_ends_the_run(
    error: Exception, catalog: list[dict[str, Any]], declared: dict[str, str]
) -> None:
    """The code is weighed against the columns the reference query names and the kind of
    drift it would take: the mart drifting somewhere else does not make the query's own
    `43`/`47` a fact about the mart."""
    engine = Answering("EXPLAIN", error, **{"system.columns": catalog})

    with pytest.raises(RuntimeError) as raised:
        collect(engine, priced_over(declared), engine="clickhouse", target="stand")

    assert raised.value is error


def test_a_drift_of_another_clock_on_the_mart_does_not_account_for_the_code() -> None:
    """Two table questions over one mart, one per clock. The plan's own question has no drift;
    the other's `gross_margin` is gone. A query of the plan's metric that names `gross_margin`
    is the gate's or the model's mistake, and `47` for it ends the run."""
    engine = Answering(
        "EXPLAIN",
        UNKNOWN_IDENTIFIER,
        **{
            "system.columns": [
                {"name": "date", "type": "Date"},
                {"name": "ts", "type": "DateTime"},
                {"name": "revenue", "type": "Decimal(18, 2)"},
            ]
        },
    )
    asked = priced_over(DECLARED)
    asked.tables.append(
        TableQuestion(
            table="dm.sales_daily",
            event_time_column="ts",
            columns={"ts": "DateTime", "gross_margin": "Decimal(18,2)"},
            timezones=["Europe/Moscow"],
            metric_refs=["metric://sales/gross_margin@1"],
        )
    )
    asked.plans[0].sql = "SELECT date, sum(gross_margin) AS m FROM dm.sales_daily GROUP BY date"

    with pytest.raises(RuntimeError) as raised:
        collect(engine, asked, engine="clickhouse", target="stand")

    assert raised.value is UNKNOWN_IDENTIFIER


def test_a_reference_query_sqlglot_cannot_read_names_no_column_to_account_for_the_code() -> None:
    engine = Answering("EXPLAIN", UNKNOWN_IDENTIFIER, **{"system.columns": DROPPED})
    asked = priced_over(DECLARED)
    asked.plans[0].sql = "SELECT sum(revenue FROM dm.sales_daily"

    with pytest.raises(RuntimeError) as raised:
        collect(engine, asked, engine="clickhouse", target="stand")

    assert raised.value is UNKNOWN_IDENTIFIER


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Code: 62. DB::Exception: Syntax error: failed at position 1. (SYNTAX_ERROR)"),
        RuntimeError("Connection refused"),
    ],
)
def test_an_estimate_that_failed_for_any_other_reason_still_ends_the_run(error: Exception) -> None:
    """A syntax error is ours and a dropped connection is the run's; neither says anything
    about the mart, drifted or not, so neither becomes a fact about it."""
    engine = Answering("EXPLAIN", error, **{"system.columns": DROPPED})

    with pytest.raises(RuntimeError) as raised:
        collect(engine, priced_over(DECLARED), engine="clickhouse", target="stand")

    assert raised.value is error


def test_the_exact_select_of_a_plan_the_mart_no_longer_fits_is_not_sent() -> None:
    """Its answer is known from the plan; asking again measures nothing and reads the mart."""
    engine = Answering("EXPLAIN", UNKNOWN_IDENTIFIER, **{"system.columns": DROPPED})
    sent: list[PlanQuestion] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        sent.append(question)
        return ExecutionFacts(metric_ref=question.metric_ref, evidence="measured")

    facts = collect(
        engine,
        priced_over(DECLARED, max_scanned_bytes=1000),
        engine="clickhouse",
        target="stand",
        run_cost=run_cost,
    )

    assert sent == []
    [execution] = facts.executions
    assert execution.checked_bounds == []
    assert execution.evidence == (
        "the exact SELECT was not run: EXPLAIN ESTIMATE of the same query answered Code 47 "
        "UNKNOWN_IDENTIFIER, and a query the mart no longer fits measures nothing; no declared "
        "runtime bound was measured"
    )


@pytest.mark.parametrize(
    ("error", "catalog"),
    [
        (UNKNOWN_IDENTIFIER, [{"name": "revenue", "type": "Decimal(18, 2)"}]),
        (ILLEGAL_TYPE, [{"name": "date", "type": "Map(String, String)"}]),
    ],
    ids=["47-gone", "43-retyped"],
)
def test_an_event_time_column_that_is_gone_leaves_the_rows_counted_and_undated(
    error: Exception, catalog: list[dict[str, Any]]
) -> None:
    """`max(date)` of a dropped `date` is `47 UNKNOWN_IDENTIFIER`, of a `date` retyped to
    what `max()` does not take `43`. The rows are still there to count; nothing dates them,
    and `check_freshness` says so instead of the run ending."""
    engine = Answering(
        "max(",
        error,
        **{"count() AS rows_seen FROM": [{"rows_seen": 91}], "system.columns": catalog},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 91)
    assert table.max_event_time is None
    assert 'SELECT count() AS rows_seen FROM "dm"."sales_daily"' in engine.asked


@pytest.mark.parametrize(
    ("error", "catalog", "declared"),
    [
        # a syntax error is ours, over a mart whose `date` is gone too
        (
            RuntimeError("Code: 62. DB::Exception: Syntax error. (SYNTAX_ERROR)"),
            [{"name": "revenue", "type": "Decimal(18, 2)"}],
            None,
        ),
        # the catalog still holds `date` as declared: the query is at fault, not the mart
        (UNKNOWN_IDENTIFIER, [{"name": "date", "type": "Date"}], None),
        (ILLEGAL_TYPE, [{"name": "date", "type": "Date"}], None),
        # a drift of the other kind does not account for the code
        (UNKNOWN_IDENTIFIER, [{"name": "date", "type": "DateTime"}], None),
        (ILLEGAL_TYPE, [{"name": "revenue", "type": "Decimal(18, 2)"}], None),
        # nor does a drift of another column
        (UNKNOWN_IDENTIFIER, [{"name": "date", "type": "Date"}], DECLARED),
    ],
    ids=[
        "syntax-over-drift",
        "47-as-declared",
        "43-as-declared",
        "47-over-a-retype",
        "43-over-a-drop",
        "another-column-gone",
    ],
)
def test_an_event_time_reading_that_failed_otherwise_still_ends_the_run(
    error: Exception, catalog: list[dict[str, Any]], declared: dict[str, str] | None
) -> None:
    engine = Answering("max(", error, **{"system.columns": catalog})

    with pytest.raises(RuntimeError) as raised:
        collect(engine, questions(declared), engine="clickhouse", target="stand")

    assert raised.value is error


def test_only_an_unfit_code_earns_the_count_question() -> None:
    """A syntax error is raised before `count()` is asked: asked anyway, a role refused
    `count()` would turn it into `access.table_unreadable` instead of a broken run."""
    error = RuntimeError("Code: 62. DB::Exception: Syntax error. (SYNTAX_ERROR)")
    engine = Answering(
        "max(", error, **{"system.columns": [{"name": "revenue", "type": "Decimal(18, 2)"}]}
    )

    with pytest.raises(RuntimeError) as raised:
        collect(engine, questions(), engine="clickhouse", target="stand")

    assert raised.value is error
    assert 'SELECT count() AS rows_seen FROM "dm"."sales_daily"' not in engine.asked


#: An own-grant row with no access type: the engine did not say what the identity may read,
#: and ClickHouse leaves out of `system.columns` a column the role may not read.
UNANSWERED_REACH = [{"access_type": None, "database": None, "table": None, "column": None}]


def test_a_column_absent_where_the_reach_is_unanswered_does_not_account_for_a_plan() -> None:
    """`dwh.physical` does not call such an absent column gone on ClickHouse, and the cost
    reading may not either: the `47` of the plan still ends the run."""
    engine = Answering(
        "EXPLAIN",
        UNKNOWN_IDENTIFIER,
        **{"system.columns": DROPPED, "system.grants": UNANSWERED_REACH},
    )

    with pytest.raises(RuntimeError) as raised:
        collect(engine, priced_over(DECLARED), engine="clickhouse", target="stand")

    assert raised.value is UNKNOWN_IDENTIFIER


def test_a_column_absent_where_the_reach_is_unanswered_does_not_account_for_max() -> None:
    """The same reading for the event-time column: a `date` the role may merely not see is
    not a `date` gone, so `max(date)` answering `47` still ends the run."""
    engine = Answering(
        "max(",
        UNKNOWN_IDENTIFIER,
        **{
            "count() AS rows_seen FROM": [{"rows_seen": 91}],
            "system.columns": [{"name": "revenue", "type": "Decimal(18, 2)"}],
            "system.grants": UNANSWERED_REACH,
        },
    )

    with pytest.raises(RuntimeError) as raised:
        collect(engine, questions(DECLARED), engine="clickhouse", target="stand")

    assert raised.value is UNKNOWN_IDENTIFIER


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


class PostgresError(RuntimeError):
    """A psycopg error as the gate holds it: the SQLSTATE in `sqlstate`, the server's text."""

    def __init__(self, sqlstate: str, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


#: What PostgreSQL 16.15 said, live, when `revenue` was dropped and when it became `text`
#: (tests/test_decision_live_postgres.py); the text quotes the query, which a sealed fact
#: must not carry.
UNDEFINED_COLUMN = PostgresError(
    "42703",
    'column "revenue" does not exist\nLINE 1: EXPLAIN (FORMAT JSON) SELECT date, store_id, '
    "sum(revenue) ...",
)
UNDEFINED_FUNCTION = PostgresError(
    "42883",
    "function sum(text) does not exist\nHINT:  No function matches the given name and argument "
    "types. You might need to add explicit type casts.",
)
PG_DECLARED = {"date": "date", "revenue": "numeric(18,2)"}
#: The catalog of a mart whose `revenue` was retyped, and of one where it is gone.
PG_RETYPED = [{"name": "date", "type": "date"}, {"name": "revenue", "type": "text"}]
PG_DROPPED = [{"name": "date", "type": "date"}]


def pg_answering(
    marker: str,
    error: Exception,
    catalog: list[dict[str, Any]],
    **overrides: list[dict[str, Any]],
) -> Answering:
    """A PostgreSQL mart holding `catalog`, every column of it readable, raising at `marker`."""
    reads = [{"name": column["name"], "ok": True} for column in catalog]
    return Answering(
        marker,
        error,
        base=postgres_engine,
        **{"format_type": catalog, "has_column_privilege(a.attrelid": reads, **overrides},
    )


def pg_priced_over(columns: dict[str, str], **budgets: int) -> EnvironmentQuestions:
    asked = priced(dialect="postgres", **budgets)
    asked.tables[0].columns = columns
    return asked


@pytest.mark.parametrize(
    ("error", "catalog", "said"),
    [
        (UNDEFINED_FUNCTION, PG_RETYPED, "SQLSTATE 42883 undefined_function"),
        (UNDEFINED_COLUMN, PG_DROPPED, "SQLSTATE 42703 undefined_column"),
    ],
)
def test_a_postgres_reference_query_the_mart_no_longer_fits_is_an_unpriced_plan(
    error: Exception, catalog: list[dict[str, Any]], said: str
) -> None:
    """PostgreSQL refuses to plan over a retyped or dropped column as ClickHouse does, in its
    own states: the plan stays unpriced and says why, in the state and its name alone."""
    engine = pg_answering("EXPLAIN", error, catalog)

    facts = collect(engine, pg_priced_over(PG_DECLARED), engine="postgres", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert facts.plans[0].evidence == (
        f"EXPLAIN answered the reference query with {said}, taken to mean the query no "
        "longer fits the mart as the engine has it (revenue drifted from the model)"
    )


def test_a_type_spelled_otherwise_than_format_type_takes_a_42883_of_the_query_s_own() -> None:
    """The Known boundary of unanswered-engine.md: a state is matched to a drift by its kind,
    not by where the query failed, and `dwh.physical` compares type names as written. `float8`
    against the catalog's `double precision` is a retype, so a `42883` that is the query's own
    (a function missing altogether) is taken to mean that drift — hence «taken to mean»."""
    error = PostgresError("42883", "function median(numeric) does not exist")
    catalog = [{"name": "date", "type": "date"}, {"name": "revenue", "type": "double precision"}]

    facts = collect(
        pg_answering("EXPLAIN", error, catalog),
        pg_priced_over({"date": "date", "revenue": "float8"}),
        engine="postgres",
        target="stand",
    )

    assert facts.plans[0].scanned_rows is None
    assert facts.plans[0].evidence == (
        "EXPLAIN answered the reference query with SQLSTATE 42883 undefined_function, taken to "
        "mean the query no longer fits the mart as the engine has it (revenue drifted from the "
        "model)"
    )


@pytest.mark.parametrize(
    ("error", "catalog", "declared"),
    [
        # `42703` is a column gone; the only drift here is a retype
        (UNDEFINED_COLUMN, PG_RETYPED, PG_DECLARED),
        # `42883` is a column retyped; the only drift here is a column gone
        (UNDEFINED_FUNCTION, PG_DROPPED, PG_DECLARED),
        # the catalog holds every column as declared: the query is at fault, not the mart
        (UNDEFINED_COLUMN, PG_RETYPED, {"date": "date", "revenue": "text"}),
        (UNDEFINED_FUNCTION, PG_RETYPED, {"date": "date", "revenue": "text"}),
        # gone, but the reference query does not read `gross_margin`
        (
            UNDEFINED_COLUMN,
            [{"name": "date", "type": "date"}, {"name": "revenue", "type": "numeric(18,2)"}],
            {**PG_DECLARED, "gross_margin": "numeric(18,2)"},
        ),
        # a syntax error is ours and a dropped connection the run's, over a drift that fits
        (PostgresError("42601", 'syntax error at or near "FROM"'), PG_DROPPED, PG_DECLARED),
        (PostgresError("08006", "server closed the connection"), PG_DROPPED, PG_DECLARED),
        # a state is what is weighed, never the text
        (RuntimeError('column "revenue" does not exist'), PG_DROPPED, PG_DECLARED),
    ],
    ids=[
        "42703-over-a-retype",
        "42883-over-a-drop",
        "42703-as-declared",
        "42883-as-declared",
        "drift-the-query-does-not-read",
        "syntax-over-a-drop",
        "connection-over-a-drop",
        "no-state-over-a-drop",
    ],
)
def test_a_postgres_state_the_drift_does_not_account_for_still_ends_the_run(
    error: Exception, catalog: list[dict[str, Any]], declared: dict[str, str]
) -> None:
    engine = pg_answering("EXPLAIN", error, catalog)

    with pytest.raises(RuntimeError) as raised:
        collect(engine, pg_priced_over(declared), engine="postgres", target="stand")

    assert raised.value is error


def test_the_exact_select_of_a_postgres_plan_the_mart_no_longer_fits_is_not_sent() -> None:
    engine = pg_answering("EXPLAIN", UNDEFINED_COLUMN, PG_DROPPED)
    sent: list[PlanQuestion] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        sent.append(question)
        return ExecutionFacts(metric_ref=question.metric_ref, evidence="measured")

    facts = collect(
        engine,
        pg_priced_over(PG_DECLARED, timeout_ms=1000),
        engine="postgres",
        target="stand",
        run_cost=run_cost,
    )

    assert sent == []
    [execution] = facts.executions
    assert execution.checked_bounds == []
    assert execution.evidence == (
        "the exact SELECT was not run: EXPLAIN of the same query answered SQLSTATE 42703 "
        "undefined_column, and a query the mart no longer fits measures nothing; no declared "
        "runtime bound was measured"
    )


def test_the_exact_select_of_a_postgres_plan_that_fits_is_still_sent() -> None:
    """The control of the test above: the same mart as declared, the same budget."""
    engine = postgres_engine(
        **postgres_plan(
            {"Plan": {"Node Type": "Seq Scan", "Relation Name": "sales_daily", "Plan Rows": 600}}
        )
    )
    sent: list[PlanQuestion] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        sent.append(question)
        return ExecutionFacts(metric_ref=question.metric_ref, evidence="measured")

    facts = collect(
        engine,
        pg_priced_over(PG_DECLARED, timeout_ms=1000),
        engine="postgres",
        target="stand",
        run_cost=run_cost,
    )

    assert [question.sql for question in sent] == [PLAN_SQL]
    assert [execution.evidence for execution in facts.executions] == ["measured"]


#: The mart's answer to the one question that counts its rows without dating them.
PG_COUNTED = {"count(*) AS rows_seen FROM": [{"rows_seen": 91}]}


@pytest.mark.parametrize(
    ("error", "catalog"),
    [
        (
            PostgresError("42703", 'column "date" does not exist'),
            [{"name": "revenue", "type": "numeric(18,2)"}],
        ),
        (
            PostgresError("42883", "function max(jsonb) does not exist"),
            [{"name": "date", "type": "jsonb"}],
        ),
    ],
    ids=["42703-gone", "42883-retyped"],
)
def test_a_postgres_event_time_column_that_no_longer_fits_leaves_the_rows_counted_and_undated(
    error: Exception, catalog: list[dict[str, Any]]
) -> None:
    """`max(date)` of a dropped `date` is `42703`, of a `date` retyped to `jsonb` `42883`
    (PostgreSQL 16.15, live). As on ClickHouse, the rows are still there to count; nothing
    dates them, and `check_freshness` says so instead of the run ending."""
    engine = pg_answering("max(", error, catalog, **PG_COUNTED)

    facts = collect(engine, questions({"date": "date"}), engine="postgres", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 91)
    assert table.max_event_time is None
    assert 'SELECT count(*) AS rows_seen FROM "dm"."sales_daily"' in engine.asked


@pytest.mark.parametrize(
    ("error", "catalog", "declared"),
    [
        # a syntax error is ours, over a mart whose `date` is gone too
        (
            PostgresError("42601", 'syntax error at or near "FROM"'),
            [{"name": "revenue", "type": "numeric(18,2)"}],
            None,
        ),
        # the catalog still holds `date` as declared: the query is at fault, not the mart
        (PostgresError("42703", 'column "date" does not exist'), PG_DROPPED, None),
        (PostgresError("42883", "function max(date) does not exist"), PG_DROPPED, None),
        # a drift of the other kind does not account for the state
        (
            PostgresError("42703", 'column "date" does not exist'),
            [{"name": "date", "type": "jsonb"}],
            None,
        ),
        (
            PostgresError("42883", "function max(jsonb) does not exist"),
            [{"name": "revenue", "type": "numeric(18,2)"}],
            None,
        ),
        # nor does a drift of another column
        (PostgresError("42703", 'column "date" does not exist'), PG_DROPPED, PG_DECLARED),
    ],
    ids=[
        "syntax-over-drift",
        "42703-as-declared",
        "42883-as-declared",
        "42703-over-a-retype",
        "42883-over-a-drop",
        "another-column-gone",
    ],
)
def test_a_postgres_event_time_reading_that_failed_otherwise_still_ends_the_run(
    error: Exception, catalog: list[dict[str, Any]], declared: dict[str, str] | None
) -> None:
    engine = pg_answering("max(", error, catalog, **PG_COUNTED)

    with pytest.raises(RuntimeError) as raised:
        collect(
            engine,
            questions({"date": "date"} if declared is None else declared),
            engine="postgres",
            target="stand",
        )

    assert raised.value is error


def test_a_postgres_column_gone_where_the_reach_is_unanswered_still_accounts_for_max() -> None:
    """Unlike ClickHouse, the PostgreSQL catalog lists every column whatever the role may
    read: a `date` it does not list is gone even where `has_column_privilege` was refused, and
    `max(date)` answering `42703` leaves the rows counted."""
    engine = pg_answering(
        "max(",
        PostgresError("42703", 'column "date" does not exist'),
        [{"name": "revenue", "type": "numeric(18,2)"}],
        **PG_COUNTED,
    )
    refusing = RefusingEngine(engine, "has_column_privilege(a.attrelid", sqlstate="42501")

    facts = collect(refusing, questions({"date": "date"}), engine="postgres", target="stand")

    table = facts.tables[0]
    assert table.column_reads is None
    assert (table.rows_seen, table.max_event_time) == (91, None)


def test_a_postgres_refusal_carried_as_psycopg2_carries_it_is_a_refusal() -> None:
    """psycopg2 keeps the SQLSTATE in `pgcode`: `42501` there is the same refusal, whatever
    language the server words it in (`lc_messages`), so the state decides, not the text."""
    error = RuntimeError("нет доступа к таблице sales_daily")
    error.pgcode = "42501"  # type: ignore[attr-defined]
    engine = Answering("EXPLAIN", error, base=postgres_engine)

    facts = collect(engine, priced(dialect="postgres"), engine="postgres", target="stand")

    assert facts.plans[0].scanned_rows is None
    assert facts.plans[0].evidence == "the engine refused EXPLAIN on the reference query"


def test_only_a_postgres_unfit_state_earns_the_count_question() -> None:
    """A syntax error is raised before `count(*)` is asked, as on ClickHouse."""
    error = PostgresError("42601", 'syntax error at or near "FROM"')
    engine = pg_answering("max(", error, [{"name": "revenue", "type": "numeric(18,2)"}])

    with pytest.raises(RuntimeError) as raised:
        collect(engine, questions({"date": "date"}), engine="postgres", target="stand")

    assert raised.value is error
    assert 'SELECT count(*) AS rows_seen FROM "dm"."sales_daily"' not in engine.asked


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


# --- a column catalog the role may not read (ADR 0010, F1) ----------------------------------


@pytest.mark.parametrize(
    "refused",
    ["FROM pg_attribute", "format_type(a.atttypid"],
    ids=["every-pg_attribute-question", "the-column-type-question-alone"],
)
def test_a_postgres_column_catalog_the_role_may_not_read_is_unknown_not_empty(
    refused: str,
) -> None:
    """`REVOKE SELECT ON pg_catalog.pg_attribute FROM PUBLIC` refuses the gate's role the
    column list (`42501`). Verifier 132 read the refusal as an empty catalog: each declared
    column gone, `physical.column_absent` (block) over a mart that holds them all."""
    engine = RefusingEngine(postgres_engine(), refused, sqlstate="42501")
    scope = questions(dict(PG_DECLARED))

    facts = collect(engine, scope, engine="postgres", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 600)
    assert table.columns is None
    assert check_physical(scope, facts) == []


def test_a_postgres_column_catalog_that_answers_no_row_is_still_an_answer() -> None:
    """The engine answered the question: a mart with none of the declared columns is what it
    said, and the column is gone, as before."""
    engine = postgres_engine(format_type=[])
    scope = questions({"date": "date"})

    facts = collect(engine, scope, engine="postgres", target="stand")

    assert facts.tables[0].columns == {}
    assert [finding.code for finding in check_physical(scope, facts)] == ["physical.column_absent"]


def test_a_clickhouse_column_catalog_the_role_may_not_read_is_unknown_not_empty() -> None:
    """`system.columns` refused while the grants put every declared column in reach: read as
    empty, the catalog made each of them gone, a block over a mart that holds them."""
    engine = RefusingEngine(clickhouse_engine(), "FROM system.columns")
    scope = questions({"date": "Date", "revenue": "Decimal(18, 2)"})

    facts = collect(engine, scope, engine="clickhouse", target="stand")

    table = facts.tables[0]
    assert table.column_reads == {"date": True, "revenue": True}
    assert table.columns is None
    assert table.layout is not None  # `system.tables` answered: only the columns are unknown
    assert check_physical(scope, facts) == []


def test_a_clickhouse_column_catalog_that_answers_no_row_is_still_an_answer() -> None:
    """Every declared column is in reach by the grants and the catalog lists none of them:
    they are gone, as before."""
    engine = clickhouse_engine(**{"system.columns": []})
    scope = questions({"date": "Date"})

    facts = collect(engine, scope, engine="clickhouse", target="stand")

    assert facts.tables[0].columns == {}
    assert [finding.code for finding in check_physical(scope, facts)] == ["physical.column_absent"]


def test_a_refused_postgres_catalog_accounts_for_no_unfit_plan() -> None:
    """The `42703` of EXPLAIN was taken to mean `revenue` gone, for the refused catalog read
    as empty said so; nothing the engine answered says it now, and the state ends the run.
    Only the column-type question is refused (the reach question still answers), so the
    catalog alone is what this pins."""
    engine = RefusingEngine(
        pg_answering("EXPLAIN", UNDEFINED_COLUMN, PG_DROPPED),
        "format_type(a.atttypid",
        sqlstate="42501",
    )

    with pytest.raises(PostgresError) as raised:
        collect(engine, pg_priced_over(PG_DECLARED), engine="postgres", target="stand")

    assert raised.value is UNDEFINED_COLUMN


def test_a_refused_postgres_catalog_accounts_for_no_undated_reading() -> None:
    """Nor does it account for the `42703` of `max(date)`: the rows are not counted undated."""
    error = PostgresError("42703", 'column "date" does not exist')
    engine = RefusingEngine(
        pg_answering("max(", error, [{"name": "revenue", "type": "numeric(18,2)"}], **PG_COUNTED),
        "format_type(a.atttypid",
        sqlstate="42501",
    )

    with pytest.raises(PostgresError) as raised:
        collect(engine, questions({"date": "date"}), engine="postgres", target="stand")

    assert raised.value is error


def test_a_refused_clickhouse_catalog_accounts_for_no_unfit_plan() -> None:
    """The ClickHouse twin: `47` from EXPLAIN ESTIMATE with `system.columns` refused and every
    column in reach by the grants ends the run."""
    engine = RefusingEngine(
        Answering("EXPLAIN", UNKNOWN_IDENTIFIER, **{"system.columns": DROPPED}),
        "FROM system.columns",
    )

    with pytest.raises(RuntimeError) as raised:
        collect(engine, priced_over(DECLARED), engine="clickhouse", target="stand")

    assert raised.value is UNKNOWN_IDENTIFIER


def test_a_refused_clickhouse_catalog_accounts_for_no_undated_reading() -> None:
    """Nor the `47` of `max(date)`: the rows are not counted undated."""
    error = RuntimeError(
        "Code: 47. DB::Exception: Unknown expression identifier `date` in scope SELECT "
        "max(date). (UNKNOWN_IDENTIFIER)"
    )
    engine = RefusingEngine(
        Answering(
            "max(",
            error,
            **{
                "count() AS rows_seen FROM": [{"rows_seen": 91}],
                "system.columns": [{"name": "revenue", "type": "Decimal(18,2)"}],
            },
        ),
        "FROM system.columns",
    )

    with pytest.raises(RuntimeError) as raised:
        collect(engine, questions({"date": "Date"}), engine="clickhouse", target="stand")

    assert raised.value is error


# --- a table catalog the role may not read (ADR 0010, F1; review r1) -------------------------

#: The question that resolves the mart's name alone, asked only when the catalog views refuse.
PG_NAME_QUESTION = "1 AS present WHERE to_regclass("


def pg_scope() -> EnvironmentQuestions:
    """The PostgreSQL mart of `PG_DECLARED`, declared as PostgreSQL: `check_access` compares
    the declared engine with the one that answered."""
    scope = questions(dict(PG_DECLARED))
    scope.engines = ["postgres"]
    return scope


def test_a_postgres_table_catalog_the_role_may_not_read_asks_the_name_itself() -> None:
    """`REVOKE SELECT ON pg_catalog.pg_tables FROM PUBLIC` refuses the presence question
    (`42501`). Read as "no row", the refusal named a mart the role reads
    `access.table_absent` (block); `to_regclass` finds it without the view."""
    engine = RefusingEngine(
        postgres_engine(**{PG_NAME_QUESTION: [{"present": 1}]}), "FROM pg_tables", sqlstate="42501"
    )
    scope = pg_scope()

    facts = collect(engine, scope, engine="postgres", target="stand")

    table = facts.tables[0]
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 600)
    assert check_access(scope, facts) == []
    [asked] = [sql for sql in engine.asked if PG_NAME_QUESTION in sql]
    assert 'to_regclass(\'"dm"."sales_daily"\') IS NOT NULL' in asked


@pytest.mark.parametrize(
    "name_answer",
    [[], None],
    ids=["no-relation-by-that-name", "the-name-question-refused-too"],
)
def test_a_refused_postgres_table_catalog_leaves_a_mart_it_cannot_find_absent(
    name_answer: list[dict[str, Any]] | None,
) -> None:
    """The name resolves to nothing, or that question is refused as well: absent, as before —
    nothing the engine answered places the mart."""
    refused = ["FROM pg_tables"] + ([PG_NAME_QUESTION] if name_answer is None else [])
    engine = RefusingEngine(
        postgres_engine(**{PG_NAME_QUESTION: name_answer or []}), *refused, sqlstate="42501"
    )
    scope = pg_scope()

    facts = collect(engine, scope, engine="postgres", target="stand")

    assert (facts.tables[0].exists, facts.tables[0].readable) == (False, False)
    assert [finding.code for finding in check_access(scope, facts)] == ["access.table_absent"]


def test_an_answered_postgres_table_catalog_is_not_asked_again() -> None:
    """The views answered "no row": that is the engine's answer, and the name question — which
    resolves any relation, an index or a sequence too — is not put in its place."""
    engine = postgres_engine(**{"pg_tables": [], PG_NAME_QUESTION: [{"present": 1}]})

    facts = collect(engine, questions(dict(PG_DECLARED)), engine="postgres", target="stand")

    assert facts.tables[0].exists is False
    assert not [sql for sql in engine.asked if PG_NAME_QUESTION in sql]


def test_a_postgres_materialized_view_counts_as_a_mart() -> None:
    """A DM served as a materialized view is in neither `pg_tables` nor `pg_views` (relkind
    `m`); read from those two alone, it was `access.table_absent` (block) — found while
    triaging review r1 of F1."""
    engine = postgres_engine(**{"pg_tables": [], "pg_matviews": [{"matviewname": "sales_daily"}]})

    facts = collect(engine, questions(), engine="greengage", target="stand")

    assert (facts.tables[0].exists, facts.tables[0].readable) == (True, True)
    [presence] = [sql for sql in engine.asked if "FROM pg_tables" in sql]
    assert "FROM pg_matviews WHERE schemaname = 'dm' AND matviewname = 'sales_daily'" in presence
