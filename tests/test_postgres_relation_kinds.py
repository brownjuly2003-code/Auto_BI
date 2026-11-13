"""ADR 0012, D2: the relations a PostgreSQL mart may be, and a column catalog nobody answered.

PostgreSQL 16 and the 9.4.26 kernel answered each case live before it was written down
(tests/test_decision_live_postgres.py repeats them on a stand), and PostgreSQL 17.12 the
`MAINTAIN` one:

* a materialized view takes no INSERT, UPDATE, DELETE or TRUNCATE whatever is granted on it
  (`42809`), while `has_table_privilege` answers the grant: its write grants are not writes.
  REFRESH changes it, which takes its ownership or, from 17, `MAINTAIN` — which `GRANT ALL`
  and membership in `pg_maintain` carry too; on any other relation `MAINTAIN` changes no row;
* a materialized view created or last refreshed `WITH NO DATA` answers every read with
  `55000` until it is refreshed, and `pg_matviews.ispopulated` is false for it;
* a foreign table is in none of `pg_tables`, `pg_views` and `pg_matviews`: `pg_class` lists it,
  with relkind `f`, and it reads like any table.

A readable mart whose column catalog the engine did not answer is `physical.not_observed` on
both engines: verifier 133 to 135 compared none of its columns and passed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_bi.severity import Severity
from auto_bi.verification.environment import (
    LayoutFacts,
    TableFacts,
    check_access,
    check_physical,
    check_security,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import (
    Case,
    _check_payload,
    probing,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_environment_probe import (
    FakeEngine,
    PostgresError,
    RefusingEngine,
    clickhouse_engine,
    pg_scope,
    postgres_engine,
    questions,
)
from tests.test_environment_question_identity import facts
from tests.test_environment_question_identity import questions as clock_questions
from tests.test_postgres_column_write_privileges import (
    MART,
    column_sql,
    no_table_writes,
    pg_questions,
    role_engine,
    table_sql,
)
from tests.test_postgres_set_role_reach import (
    cost_recorder,
    reachable,
    reachable_on,
    reachable_sql,
    role_row,
    second_mart,
    with_runtime,
)

MATVIEW = (
    "a materialized view, which takes no INSERT, UPDATE, DELETE or TRUNCATE whatever is granted "
    "on it"
)
#: The clean evidence where the in-scope mart is a materialized view: a grant on it is no write.
MATVIEW_CLEAN = (
    "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart but a materialized "
    "view, no MAINTAIN privilege on a materialized view and no INSERT or UPDATE privilege on "
    "any of their columns"
)
EVERY_WRITE = {"insert": True, "update": True, "delete": True, "truncate": True}
#: The clean evidence where no in-scope mart is a materialized view.
TABLE_CLEAN = (
    "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart and no INSERT or "
    "UPDATE privilege on any of their columns"
)


def granted(relkind: str | None, **answers: Any) -> list[dict[str, Any]]:
    """The current role's table-level answer for a relation of `relkind`."""
    return [{**no_table_writes(), **answers, "relkind": relkind}]


def kind_engine(relkind: str | None, **answers: Any) -> FakeEngine:
    """`role_engine` with the mart of the given kind; its column write grants all on."""
    engine = role_engine(insert=True, update=True)
    engine.answers[table_sql(MART)] = granted(relkind, **answers)
    return engine


# --- write grants on a materialized view --------------------------------------------------


def test_write_grants_on_a_materialized_view_are_not_writes() -> None:
    """`GRANT INSERT, UPDATE, DELETE, TRUNCATE ON dm.mv` and column grants: `has_table_privilege`
    answers them all true, and each write is `42809` (16 and 9.4.26, live). Verifier 135 graded
    the role `access.role_writable` (block); the column question is not asked any more."""
    engine = kind_engine("m", **EVERY_WRITE)
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is True
    assert observed.role.evidence == (
        f"{MATVIEW_CLEAN}; SET ROLE reaches no other role; {MART} is {MATVIEW}"
    )
    assert check_access(scope, observed) == []
    assert column_sql(MART) not in engine.asked


def test_the_owner_of_a_materialized_view_still_writes_it() -> None:
    """Its owner refreshes, alters and drops it: ownership is weighed as for any mart."""
    engine = kind_engine("m", owns_mart=True)
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == (
        f"granted ownership of {MART} by mart_owner, held through current role gate_ro (USAGE)"
    )


@pytest.mark.parametrize("relkind", ["r", "p", "v", "f", None])
def test_write_grants_on_any_other_relation_are_writes(relkind: str | None) -> None:
    """A table, a partitioned table, an updatable view and a foreign table whose wrapper takes
    writes all write what is granted; so does a relation the catalog did not place. Their
    column grants are asked as before: a materialized view's alone are not."""
    engine = kind_engine(relkind, insert=True)
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == f"granted INSERT on {MART}; UPDATE on columns of {MART}"
    assert column_sql(MART) in engine.asked


def test_the_clean_evidence_names_each_materialized_view_once() -> None:
    """Two questions over one view name it once; a table beside it adds no name."""
    engine = kind_engine("m", **EVERY_WRITE)
    engine.answers[table_sql("dm.stores")] = granted("r")
    engine.answers[column_sql("dm.stores")] = [{"column_insert": False, "column_update": False}]
    scope = second_mart(pg_questions())
    scope.tables.append(scope.tables[0].model_copy(update={"event_time_column": "shipped_at"}))

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is True
    assert observed.role.evidence == (
        f"{MATVIEW_CLEAN}; SET ROLE reaches no other role; {MART} is {MATVIEW}"
    )


def test_two_materialized_views_are_named_together() -> None:
    engine = kind_engine("m", insert=True)
    engine.answers[table_sql("dm.stores")] = granted("m", truncate=True)
    scope = second_mart(pg_questions())

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is True
    assert observed.role.evidence == (
        f"{MATVIEW_CLEAN}; SET ROLE reaches no other role; {MART}, dm.stores are materialized "
        "views, which take no INSERT, UPDATE, DELETE or TRUNCATE whatever is granted on them"
    )


@pytest.mark.parametrize(
    "grants",
    [{"maintain": True}, {"maintain": "t", **EVERY_WRITE}],
    ids=["grant-maintain", "grant-all"],
)
def test_maintain_on_a_materialized_view_is_a_write(grants: dict[str, Any]) -> None:
    """PostgreSQL 17.12, live: `GRANT ALL` on a view carries `MAINTAIN`, as `GRANT MAINTAIN`
    and membership in `pg_maintain` do, and its holder refreshes the view — recomputes it, or
    `WITH NO DATA` empties it — without owning it. The write grants beside it are still none."""
    engine = kind_engine("m", **grants)
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == f"granted MAINTAIN on {MART}"
    assert [(item.code, item.severity) for item in check_access(scope, observed)] == [
        ("access.role_writable", Severity.BLOCK)
    ]


def test_an_unanswered_maintain_on_a_materialized_view_is_a_gap() -> None:
    """Below 17 the question answers false, not nothing: a missing answer is a gap."""
    engine = kind_engine("m", maintain=None)

    observed = collect(engine, pg_questions(), engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == (
        f"unreported MAINTAIN privilege on {MART}, so read-only access is not established"
    )


#: The spellings of a boolean answer `_truthy` reads: psycopg answers a `bool`; a text result or
#: another driver answers `t`/`f`, `true`/`false` or `1`/`0`.
MAINTAIN_ANSWERS = [
    *((value, True) for value in (True, "t", "true", 1, "1")),
    *((value, False) for value in (False, "f", "false", 0, "0")),
]


@pytest.mark.parametrize("reached", [False, True], ids=["current-role", "set-role"])
@pytest.mark.parametrize(
    "maintain,held",
    MAINTAIN_ANSWERS,
    ids=[f"{'held' if held else 'not-held'}-{value!r}" for value, held in MAINTAIN_ANSWERS],
)
def test_a_maintain_answer_is_read_as_any_privilege_answer(
    maintain: object, held: bool, reached: bool
) -> None:
    """A string `f` or `0` holds no `MAINTAIN`, though a bare truth test would take it for one,
    and `true` or `1` holds it: the answer is read as every privilege answer is, on both paths."""
    engine = kind_engine("m", **({} if reached else {"maintain": maintain}))
    if reached:
        reachable(engine, [{"name": "writer", "superuser": False}])
        row = {**role_row("writer"), "maintain": maintain, "relkind": "m"}
        reachable_on(engine, MART, ["writer"], [row])

    observed = collect(engine, pg_questions(), engine="greengage", target="stand")

    through = " through SET ROLE writer" if reached else ""
    reach = (
        ", for this role or any role SET ROLE reaches (writer)"
        if reached
        else "; SET ROLE reaches no other role"
    )
    assert observed.role.read_only is not held
    assert observed.role.evidence == (
        f"granted MAINTAIN on {MART}{through}"
        if held
        else f"{MATVIEW_CLEAN}{reach}; {MART} is {MATVIEW}"
    )


@pytest.mark.parametrize("maintain", [True, None], ids=["held", "unanswered"])
@pytest.mark.parametrize("relkind", ["r", "p", "v", "f", None])
def test_maintain_on_any_other_relation_writes_nothing(
    relkind: str | None, maintain: bool | None
) -> None:
    """What `MAINTAIN` allows there — VACUUM, ANALYZE, CLUSTER, REINDEX, LOCK TABLE — changes
    no row (17.12 answers it true on a table under `GRANT ALL`): neither held nor unanswered
    is weighed."""
    engine = role_engine()
    engine.answers[table_sql(MART)] = granted(relkind, maintain=maintain)
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is True
    assert observed.role.evidence == f"{TABLE_CLEAN}; SET ROLE reaches no other role"


def test_a_table_write_beside_a_materialized_view_still_blocks() -> None:
    engine = kind_engine("m", **EVERY_WRITE)
    engine.answers[table_sql("dm.stores")] = granted("r", delete=True)
    engine.answers[column_sql("dm.stores")] = [{"column_insert": False, "column_update": False}]
    scope = second_mart(pg_questions())

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == "granted DELETE on dm.stores"


def test_a_role_set_role_reaches_writes_no_materialized_view_by_a_grant() -> None:
    engine = kind_engine("m")
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(
        engine,
        MART,
        ["writer"],
        [{**role_row("writer", column_insert=True, **EVERY_WRITE), "relkind": "m"}],
    )
    scope = pg_questions()
    run_cost, calls = cost_recorder(with_runtime(scope))

    observed = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert observed.role.read_only is True
    assert observed.role.evidence == (
        f"{MATVIEW_CLEAN}, for this role or any role SET ROLE reaches (writer); {MART} is "
        f"{MATVIEW}"
    )
    assert engine.asked.count(reachable_sql(MART, ["writer"])) == 1
    assert len(calls) == 1  # read-only: the exact SELECT is sent


def test_a_role_set_role_reaches_still_writes_a_materialized_view_it_owns() -> None:
    engine = kind_engine("m")
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [{**role_row("writer", owns_mart=True), "relkind": "m"}])
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == (
        f"granted ownership of {MART} by mart_owner, held through SET ROLE writer (USAGE)"
    )


def test_a_role_set_role_reaches_writes_a_materialized_view_it_may_maintain() -> None:
    """A `NOINHERIT` member of `pg_maintain` answers its own question false, and `pg_maintain`,
    which `SET ROLE` reaches, true (17.12, live): the reached role's `MAINTAIN` is a write as
    its write grants are."""
    engine = kind_engine("m")
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [{**role_row("writer", maintain=True), "relkind": "m"}])

    observed = collect(engine, pg_questions(), engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == f"granted MAINTAIN on {MART} through SET ROLE writer"


def test_a_reachable_role_unanswered_maintain_on_a_materialized_view_is_a_gap() -> None:
    engine = kind_engine("m")
    reachable(engine, [{"name": "writer", "superuser": False}])
    row = {**role_row("writer"), "relkind": "m"}
    del row["maintain"]
    reachable_on(engine, MART, ["writer"], [row])

    observed = collect(engine, pg_questions(), engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == (
        f"unreported MAINTAIN privilege of writer on {MART}, so read-only access is not "
        "established"
    )


def test_a_reachable_role_maintain_on_a_table_writes_nothing() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [{**role_row("writer", maintain=True), "relkind": "r"}])

    observed = collect(engine, pg_questions(), engine="greengage", target="stand")

    assert observed.role.read_only is True
    assert observed.role.evidence == (
        f"{TABLE_CLEAN}, for this role or any role SET ROLE reaches (writer)"
    )


def test_a_reachable_role_answer_without_its_grants_is_still_unreported() -> None:
    """A view row names the kind, not the grants: a row of a table missing them stays a gap."""
    engine = kind_engine("m")
    reachable(engine, [{"name": "writer", "superuser": False}])
    row = {**role_row("writer"), "relkind": "r"}
    del row["truncate"]
    reachable_on(engine, MART, ["writer"], [row])
    scope = pg_questions()

    observed = collect(engine, scope, engine="greengage", target="stand")

    assert observed.role.read_only is False
    assert observed.role.evidence == (
        f"unreported table TRUNCATE privileges of writer on {MART}, so read-only access is not "
        "established"
    )


# --- a materialized view created WITH NO DATA ---------------------------------------------

#: What PostgreSQL 16 said, live, to `count(*)` over a view created `WITH NO DATA`.
NOT_POPULATED = PostgresError("55000", 'materialized view "sales_daily" has not been populated')
POPULATED_QUESTION = (
    "SELECT ispopulated FROM pg_matviews WHERE schemaname = 'dm' AND matviewname = 'sales_daily'"
)
READS = [{"name": "date", "ok": True}, {"name": "revenue", "ok": True}]


class Raising(FakeEngine):
    """Raises the error of the longest marker a question contains; answers the rest."""

    def __init__(self, errors: dict[str, Exception], **overrides: list[dict[str, Any]]) -> None:
        super().__init__(
            postgres_engine(**{"has_column_privilege(a.attrelid": READS, **overrides}).answers
        )
        self.errors = errors

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        markers = [marker for marker in self.errors if marker in sql]
        if markers:
            self.asked.append(sql)
            raise self.errors[max(markers, key=len)]
        return super().__call__(sql)


def unpopulated(**overrides: list[dict[str, Any]]) -> Raising:
    return Raising(
        {"count(*)": NOT_POPULATED},
        **{POPULATED_QUESTION: [{"ispopulated": False}], **overrides},
    )


def test_a_materialized_view_with_no_data_is_unpopulated_not_a_broken_run() -> None:
    """Verifier 135 let the `55000` end the run (exit 2). The view is there and nothing reads
    from it until its owner refreshes it: `access.table_unpopulated` (block)."""
    engine = unpopulated()
    scope = pg_scope()
    run_cost, calls = cost_recorder(with_runtime(scope))

    observed = collect(engine, scope, engine="postgres", target="stand", run_cost=run_cost)

    [table] = observed.tables
    assert (table.exists, table.readable, table.populated) == (True, False, False)
    assert (table.rows_seen, table.max_event_time, table.columns) == (0, None, None)
    assert table.column_reads == {"date": True, "revenue": True}
    assert table.row_policies == []
    assert [(item.code, item.severity) for item in check_access(scope, observed)] == [
        ("access.table_unpopulated", Severity.BLOCK)
    ]
    [finding] = check_access(scope, observed)
    assert finding.subject == "dm.sales_daily"
    assert finding.detail == (
        "dm.sales_daily on stand is a materialized view that holds no data (created or last "
        "refreshed WITH NO DATA): every read of it fails until it is refreshed, so the metrics "
        "metric://sales/gross_margin_pct@3 cannot be computed there"
    )
    assert check_physical(scope, observed) == []
    assert check_security(scope, observed) == []
    assert engine.asked.count(POPULATED_QUESTION) == 1
    assert not any("format_type" in sql for sql in engine.asked)
    assert calls == [] and observed.executions == []


def test_the_view_is_asked_about_after_a_count_with_no_date_too() -> None:
    """`max()` of an event-time column that is gone fails at parse time (`42703`), before the
    view is opened; the `count(*)` asked then is what meets the `55000`."""
    engine = Raising(
        {
            'max("date")': PostgresError("42703", 'column "date" does not exist'),
            "SELECT count(*) AS rows_seen FROM": NOT_POPULATED,
        },
        **{POPULATED_QUESTION: [{"ispopulated": False}]},
    )
    scope = pg_scope()

    observed = collect(engine, scope, engine="postgres", target="stand")

    assert observed.tables[0].populated is False
    assert [item.code for item in check_access(scope, observed)] == ["access.table_unpopulated"]


@pytest.mark.parametrize(
    "answer",
    [[{"ispopulated": True}], [{"ispopulated": None}], [{}], [], None],
    ids=["populated", "null", "no-column", "not-a-materialized-view", "refused"],
)
@pytest.mark.parametrize("after_unfit", [False, True], ids=["first-count", "second-count"])
def test_a_55000_the_catalog_does_not_confirm_still_ends_the_run(
    answer: list[dict[str, Any]] | None, after_unfit: bool
) -> None:
    """`55000` is `object_not_in_prerequisite_state`, not the view's alone: only `ispopulated`
    false makes it an unpopulated view, and anything else is the error it was."""
    errors: dict[str, Exception] = (
        {
            'max("date")': PostgresError("42703", 'column "date" does not exist'),
            "SELECT count(*) AS rows_seen FROM": NOT_POPULATED,
        }
        if after_unfit
        else {"count(*)": NOT_POPULATED}
    )
    inner = Raising(errors, **{POPULATED_QUESTION: answer or []})
    engine = (
        RefusingEngine(inner, POPULATED_QUESTION, sqlstate="42501") if answer is None else inner
    )

    with pytest.raises(PostgresError) as raised:
        collect(engine, pg_scope(), engine="postgres", target="stand")

    assert raised.value is NOT_POPULATED
    assert inner.asked.count(POPULATED_QUESTION) == (answer is not None)


@pytest.mark.parametrize("state", ["57014", "25P02", "42P01", None])
def test_no_other_state_asks_whether_the_view_is_populated(state: str | None) -> None:
    error = PostgresError(state, "canceled") if state else RuntimeError("connection lost")
    engine = Raising({"count(*)": error}, **{POPULATED_QUESTION: [{"ispopulated": False}]})

    try:
        observed = collect(engine, pg_scope(), engine="postgres", target="stand")
    except (PostgresError, RuntimeError) as raised:
        assert raised is error
    else:  # `42P01` is a refusal: the mart is read as unreadable, as before
        assert state == "42P01"
        assert (observed.tables[0].readable, observed.tables[0].populated) == (False, None)
    assert POPULATED_QUESTION not in engine.asked


def pg_case(tmp_path: Path) -> Case:
    """The gate's own case, its marts declared on PostgreSQL."""
    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "postgres"
    return Case(tmp_path).with_candidate(model)


def test_the_gate_blocks_on_an_unpopulated_view_and_records_it(tmp_path: Path) -> None:
    observed = collect(unpopulated(), pg_scope(), engine="postgres", target="127.0.0.1:5432/dm")

    report = pg_case(tmp_path).run(probe=probing(observed))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.table_unpopulated"
    payload = _check_payload(tmp_path, report, "dwh.access")
    assert [table["populated"] for table in payload["facts"]["tables"]] == [False]
    assert [item["code"] for item in payload["findings"]] == ["access.table_unpopulated"]
    # A mart nothing reads from has no columns to compare: `check_access` said so already
    assert status_of(report, "dwh.physical") is CheckStatus.PASS
    assert summary_of(report, "dwh.physical") == "physical.not_run"


def test_every_other_reading_records_populated_as_unknown(tmp_path: Path) -> None:
    observed = collect(postgres_engine(), pg_scope(), engine="postgres", target="stand")

    report = pg_case(tmp_path).run(probe=probing(observed))

    payload = _check_payload(tmp_path, report, "dwh.access")
    assert [table["populated"] for table in payload["facts"]["tables"]] == [None]


# --- a foreign table -----------------------------------------------------------------------

#: The branch of the presence question that alone lists a foreign table.
FOREIGN = (
    "UNION ALL SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind = 'f' AND n.nspname = 'dm' AND c.relname = 'sales_daily'"
)


def test_a_foreign_table_counts_as_a_mart() -> None:
    """None of the three catalog views lists it; read from them alone it was
    `access.table_absent` (block). It reads, and its columns answer, like any table's."""
    engine = postgres_engine(
        **{
            "pg_tables": [],
            FOREIGN: [{"relname": "sales_daily"}],
            "format_type": [
                {"name": "date", "type": "date"},
                {"name": "revenue", "type": "numeric(18,2)"},
            ],
            "has_column_privilege(a.attrelid": READS,
        }
    )
    scope = pg_scope()

    observed = collect(engine, scope, engine="postgres", target="stand")

    [table] = observed.tables
    assert (table.exists, table.readable, table.rows_seen) == (True, True, 600)
    assert table.columns == {"date": "date", "revenue": "numeric(18,2)"}
    assert check_access(scope, observed) == []
    assert check_physical(scope, observed) == []
    [presence] = [sql for sql in engine.asked if "FROM pg_tables" in sql]
    assert presence.endswith(FOREIGN)


# --- a column catalog nobody answered: physical.not_observed ------------------------------


def unanswered(**update: Any) -> TableFacts:
    reading = TableFacts(
        table="dm.sales_daily",
        event_time_column="booked_at",
        exists=True,
        readable=True,
        rows_seen=1,
        max_event_time="2026-08-25",
    )
    return reading.model_copy(update=update)


def test_the_unobserved_columns_are_named_with_the_check_that_did_not_run() -> None:
    scope = clock_questions("booked_at")
    observed = facts(unanswered())

    [finding] = check_physical(scope, observed)

    declared = ", ".join(sorted(scope.tables[0].columns))
    assert (finding.code, finding.severity, finding.subject) == (
        "physical.not_observed",
        Severity.REVIEW,
        "dm.sales_daily",
    )
    assert finding.detail == (
        f"{observed.target} did not answer which columns dm.sales_daily has: "
        f"{observed.role.name} cannot read that from this engine's catalog, so the declared "
        f"types of {declared} were not compared with the engine's — the check did not run "
        "rather than passing"
    )


@pytest.mark.parametrize(
    "update",
    [{"readable": False}, {"exists": False, "readable": False}, {"columns": {}}],
    ids=["unreadable", "absent", "answered-empty"],
)
def test_no_unobserved_finding_where_access_or_an_answer_says_more(
    update: dict[str, Any],
) -> None:
    scope = clock_questions("booked_at")

    codes = [item.code for item in check_physical(scope, facts(unanswered(**update)))]

    assert "physical.not_observed" not in codes


def test_no_unobserved_finding_where_no_column_is_declared() -> None:
    scope = clock_questions("booked_at")
    scope.tables[0].columns = {}

    assert check_physical(scope, facts(unanswered())) == []


def test_a_layout_drift_is_still_reported_beside_unobserved_columns() -> None:
    """ClickHouse answered `system.tables` and refused `system.columns`: the layout is
    compared, the columns are not."""
    engine = RefusingEngine(
        clickhouse_engine(**{"system.tables": [{"engine": "Log", "sorting_key": ""}]}),
        "FROM system.columns",
    )
    scope = questions({"date": "Date"})
    scope.tables[0].layout.table_engine = "MergeTree"

    observed = collect(engine, scope, engine="clickhouse", target="stand")

    assert observed.tables[0].layout == LayoutFacts(
        table_engine="Log", sorting_key=[], partition_key="", distribution_key=None
    )
    assert [(item.code, item.severity) for item in check_physical(scope, observed)] == [
        ("physical.not_observed", Severity.REVIEW),
        ("physical.layout_drift", Severity.REVIEW),
    ]
