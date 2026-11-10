"""T-34: a role reachable through `SET ROLE` cannot hide a write privilege.

The privilege functions answer for `current_user` and what it inherits, so a `NOINHERIT`
member of a writable group answers every own write question false while `SET ROLE writer`
writes — PostgreSQL 16.15 answered exactly that live, and so did the same shape through a
`NOINHERIT` intermediate role, through the predefined `pg_write_all_data`, through a
`SUPERUSER NOLOGIN` group, and through `SET ROLE NONE` back to a session user that wrote
on its own. The collector therefore asks which roles the session user can switch into,
and asks them the same write questions it asks the current role.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    ExecutionFacts,
    PlanQuestion,
    RoleFacts,
    TableQuestion,
    check_access,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import Severity
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, probing, semantic_model, status_of, summary_of
from tests.test_environment_probe import (
    WRITE_STATEMENT,
    FakeEngine,
    RefusingEngine,
    clickhouse_engine,
    postgres_engine,
    questions,
)
from tests.test_postgres_column_write_privileges import (
    column_sql,
    no_table_writes,
    pg_questions,
    role_engine,
    table_sql,
)

MART = "dm.sales_daily"
CLEAN = (
    "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart "
    "and no INSERT or UPDATE privilege on any of their columns"
)


# --- the questions, pinned here rather than imported from the module -----------------


def literal(value: str) -> str:
    """A single-quoted literal with the quote doubled — how a role name is spelled."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def reach_sql() -> str:
    """The reachability question: the server itself chooses `'SET'` or `'MEMBER'`.

    A role the current role merely inherits stays only when it is a superuser:
    membership passes privileges on, but not the `SUPERUSER` attribute.
    """
    return (
        "SELECT r.rolname AS name, r.rolsuper AS superuser FROM pg_roles r "
        "WHERE (r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE')) AND "
        "(r.rolname = session_user OR pg_has_role(session_user, r.oid, "
        "CASE WHEN current_setting('server_version_num')::int >= 160000 "
        "THEN 'SET' ELSE 'MEMBER' END)) ORDER BY r.rolname"
    )


def reachable_sql(mart: str, roles: list[str]) -> str:
    """The per-mart write question for the roles `SET ROLE` reaches, in their order."""
    mart = '"' + mart.replace(".", '"."') + '"'
    privileges = ", ".join(
        f"has_table_privilege(r.oid, {literal(mart)}, {literal(privilege)}) AS {privilege.lower()}"
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
    )
    columns = ", ".join(
        f"has_any_column_privilege(r.oid, {literal(mart)}, {literal(privilege)}) AS "
        f"column_{privilege.lower()}"
        for privilege in ("INSERT", "UPDATE")
    )
    listed = ", ".join(literal(role) for role in roles)
    return (
        f"SELECT r.rolname AS name, {privileges}, {columns}, "
        "pg_get_userbyid(c.relowner) AS owner_name, "
        "pg_has_role(r.oid, c.relowner, 'USAGE') AS owns_mart, "
        "pg_get_userbyid(n.nspowner) AS schema_owner_name, "
        "pg_has_role(r.oid, n.nspowner, 'USAGE') AS owns_schema "
        f"FROM pg_roles r LEFT JOIN pg_class c ON c.oid = to_regclass({literal(mart)}) "
        "LEFT JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE r.rolname IN ({listed}) ORDER BY r.rolname"
    )


def reachable(engine: FakeEngine, rows: list[dict[str, Any]]) -> None:
    engine.answers[reach_sql()] = rows


def reachable_on(
    engine: FakeEngine, mart: str, roles: list[str], rows: list[dict[str, Any]]
) -> None:
    engine.answers[reachable_sql(mart, roles)] = rows


def role_row(name: str, **grants: bool) -> dict[str, Any]:
    """A per-role answer row: every write question false unless a test turns one on."""
    row: dict[str, Any] = {
        **no_table_writes(),
        "name": name,
        "insert": False,
        "update": False,
        "delete": False,
        "truncate": False,
        "column_insert": False,
        "column_update": False,
    }
    row.update(grants)
    return row


def none_granted() -> list[dict[str, Any]]:
    """The current role's own table-level answer when it holds nothing."""
    return [no_table_writes()]


def second_mart(scope: EnvironmentQuestions, table: str = "dm.stores") -> EnvironmentQuestions:
    scope.tables.append(TableQuestion(table=table, event_time_column="date"))
    return scope


def with_runtime(scope: EnvironmentQuestions) -> list[str]:
    """An eligible exact runtime probe, as the neighbouring runtime tests attach one."""
    sql = f"SELECT sum(revenue) FROM {MART}"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=MART,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    return [sql]


def cost_recorder(
    sql: list[str],
) -> tuple[Callable[[PlanQuestion], ExecutionFacts], list[str]]:
    calls: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        calls.append(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    return run_cost, calls


def assert_blocks(
    engine: FakeEngine | RefusingEngine,
    scope: EnvironmentQuestions,
    sql: list[str],
    evidence: str,
) -> EnvironmentFacts:
    """Collect with a runtime probe attached and assert the withheld-SQL block outcome."""
    run_cost, calls = cost_recorder(sql)
    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert facts.role.read_only is False
    assert facts.role.evidence == evidence
    assert calls == []
    assert facts.executions == []
    assert [(item.code, item.severity) for item in check_access(scope, facts)] == [
        ("access.role_writable", Severity.BLOCK)
    ]
    return facts


# --- a writable role reached through SET ROLE --------------------------------------


def test_a_noinherit_member_blocks_through_its_writable_group() -> None:
    """Every own answer false — the shape PostgreSQL 16.15 answered for a `NOINHERIT`
    member — while the group it may switch into holds table `INSERT`."""
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [role_row("writer", insert=True)])
    scope = pg_questions()

    facts = assert_blocks(
        engine, scope, with_runtime(scope), f"granted INSERT on {MART} through SET ROLE writer"
    )
    assert facts.role.name == "gate_ro"
    assert engine.asked.count(reach_sql()) == 1
    assert engine.asked.count(reachable_sql(MART, ["writer"])) == 1


def test_a_column_only_writer_reached_through_set_role_blocks() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "colwriter", "superuser": False}])
    reachable_on(engine, MART, ["colwriter"], [role_row("colwriter", column_update=True)])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        f"granted UPDATE on columns of {MART} through SET ROLE colwriter",
    )


def test_a_table_write_is_not_reported_again_for_the_same_roles_columns() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [role_row("writer", update=True, column_update=True)])
    scope = pg_questions()

    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted UPDATE on {MART} through SET ROLE writer"


def test_the_session_user_reached_with_set_role_none_blocks() -> None:
    """A role default can park a writable session user behind a select-only current
    role; `SET ROLE NONE` returns to it, so it is asked about by name."""
    engine = role_engine()
    engine.answers["current_user AS name"] = [
        {"name": "reader", "superuser": False, "bypassrls": False}
    ]
    reachable(engine, [{"name": "login", "superuser": False}])
    reachable_on(engine, MART, ["login"], [role_row("login", insert=True)])
    scope = pg_questions()

    facts = assert_blocks(
        engine, scope, with_runtime(scope), f"granted INSERT on {MART} through SET ROLE login"
    )
    assert facts.role.name == "reader"


def test_a_reachable_superuser_blocks_without_privilege_questions() -> None:
    engine = role_engine()
    reachable(
        engine,
        [{"name": "plain", "superuser": False}, {"name": "su", "superuser": True}],
    )
    scope = pg_questions()

    assert_blocks(engine, scope, with_runtime(scope), "granted SET ROLE to superuser su")
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


def test_an_inheriting_member_of_a_superuser_group_blocks() -> None:
    """`SUPERUSER` is a role attribute membership does not pass on. An INHERIT member
    of a `SUPERUSER NOLOGIN` group with no grants of its own answers every own write
    question false and holds `'USAGE'` on the group, so only keeping superusers in
    the reachability question — PostgreSQL 16.15 answered exactly that live — catches
    `SET ROLE su_grp` followed by a write."""
    engine = role_engine()
    reachable(engine, [{"name": "su_grp", "superuser": True}])
    scope = pg_questions()

    assert_blocks(engine, scope, with_runtime(scope), "granted SET ROLE to superuser su_grp")
    asked = [sql for sql in engine.asked if sql == reach_sql()]
    assert len(asked) == 1
    assert "(r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE'))" in asked[0]
    # a superuser is graded by its flag alone; no per-mart privilege question follows
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


# --- one question per mart, every named role in it ----------------------------------


def test_two_marts_two_roles_one_reachable_role_question_per_mart() -> None:
    engine = role_engine()
    second = "dm.stores"
    engine.answers[table_sql(second)] = none_granted()
    engine.answers[column_sql(second)] = [{"column_insert": False, "column_update": False}]
    reachable(
        engine,
        [{"name": "reader2", "superuser": False}, {"name": "writer", "superuser": False}],
    )
    reachable_on(engine, MART, ["reader2", "writer"], [role_row("reader2"), role_row("writer")])
    reachable_on(
        engine,
        second,
        ["reader2", "writer"],
        [role_row("reader2"), role_row("writer", truncate=True)],
    )
    scope = second_mart(pg_questions())

    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted TRUNCATE on {second} through SET ROLE writer"
    assert engine.asked.count(reachable_sql(MART, ["reader2", "writer"])) == 1
    assert engine.asked.count(reachable_sql(second, ["reader2", "writer"])) == 1


def test_two_questions_on_one_mart_ask_one_reachable_role_question() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [role_row("writer")])
    scope = pg_questions()
    scope.tables.append(TableQuestion(table=MART, event_time_column="shipped_at"))

    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is True
    assert engine.asked.count(reachable_sql(MART, ["writer"])) == 1
    # the current role's own questions still run once per table question
    assert engine.asked.count(table_sql(MART)) == 2
    assert engine.asked.count(column_sql(MART)) == 2


def test_the_reachability_question_is_sent_once_with_the_exact_text() -> None:
    engine = role_engine()
    second = "dm.stores"
    engine.answers[table_sql(second)] = none_granted()
    engine.answers[column_sql(second)] = [{"column_insert": False, "column_update": False}]
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [role_row("writer")])
    reachable_on(engine, second, ["writer"], [role_row("writer")])

    collect(engine, second_mart(pg_questions()), engine="greengage", target="stand")

    asked = [sql for sql in engine.asked if sql == reach_sql()]
    assert len(asked) == 1
    for fragment in (
        "session_user",
        "(r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE'))",
        "current_setting('server_version_num')::int >= 160000",
        "'SET'",
        "'MEMBER'",
    ):
        assert fragment in asked[0]


# --- false-reject controls ----------------------------------------------------------


def test_an_empty_reachability_answer_stays_read_only() -> None:
    """No marker answers the reachability question, so the engine returns no role —
    which is also the server's own answer for a membership granted `WITH SET FALSE`
    (PostgreSQL 16.15, live), so this same control covers that negative."""
    engine = role_engine()
    scope = pg_questions()
    sql = with_runtime(scope)
    run_cost, calls = cost_recorder(sql)

    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert facts.role.read_only is True
    assert facts.role.evidence == f"{CLEAN}; SET ROLE reaches no other role"
    assert check_access(scope, facts) == []
    assert calls == sql  # an eligible exact runtime probe still runs
    assert len(facts.executions) == 1
    assert reach_sql() in engine.asked


def test_reachable_roles_that_cannot_write_stay_read_only() -> None:
    engine = role_engine()
    reachable(
        engine,
        [{"name": "mid", "superuser": False}, {"name": "reader2", "superuser": False}],
    )
    reachable_on(engine, MART, ["mid", "reader2"], [role_row("mid"), role_row("reader2")])
    scope = pg_questions()
    sql = with_runtime(scope)
    run_cost, calls = cost_recorder(sql)

    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        f"{CLEAN}, for this role or any role SET ROLE reaches (mid, reader2)"
    )
    assert check_access(scope, facts) == []
    assert calls == sql
    assert len(facts.executions) == 1


# --- unanswered reachability or reachable-role questions ----------------------------


def test_a_refused_reachability_question_never_establishes_read_only() -> None:
    engine = RefusingEngine(role_engine(), reach_sql(), sqlstate="42501")
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        "unreported roles reachable through SET ROLE, so read-only access is not established",
    )
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"superuser": False}, id="missing-name"),
        pytest.param({"name": None, "superuser": False}, id="null-name"),
        pytest.param({"name": "", "superuser": False}, id="empty-name"),
    ],
)
def test_a_nameless_reachable_role_never_establishes_read_only(row: dict[str, Any]) -> None:
    engine = role_engine()
    reachable(engine, [row])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        "unreported name of a role reachable through SET ROLE, so read-only access "
        "is not established",
    )
    # a row with no name is not a role to ask the write questions about
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


def test_two_nameless_reachable_roles_name_their_count() -> None:
    engine = role_engine()
    reachable(engine, [{"superuser": False}, {"name": "", "superuser": False}])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        "unreported names of 2 roles reachable through SET ROLE, "
        "so read-only access is not established",
    )
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


def test_a_null_superuser_flag_of_a_reachable_role_never_establishes_read_only() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": None}])
    reachable_on(engine, MART, ["writer"], [role_row("writer")])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        "unreported superuser status of writer, so read-only access is not established",
    )
    # the remaining questions are still asked
    assert engine.asked.count(reachable_sql(MART, ["writer"])) == 1


def test_a_refused_reachable_role_privilege_question_never_establishes_read_only() -> None:
    engine = role_engine()
    reachable(
        engine,
        [{"name": "colwriter", "superuser": False}, {"name": "writer", "superuser": False}],
    )
    engine = RefusingEngine(engine, reachable_sql(MART, ["colwriter", "writer"]), sqlstate="42501")
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        "unreported write privileges of colwriter, writer on "
        f"{MART}; ownership of {MART} for SET ROLE colwriter; "
        f"schema ownership of {MART} for SET ROLE colwriter; "
        f"ownership of {MART} for SET ROLE writer; "
        f"schema ownership of {MART} for SET ROLE writer, so read-only access is not established",
    )


def test_a_reachable_role_omitted_from_a_privilege_answer_never_establishes_read_only() -> None:
    engine = role_engine()
    reachable(
        engine,
        [{"name": "mid", "superuser": False}, {"name": "writer", "superuser": False}],
    )
    reachable_on(engine, MART, ["mid", "writer"], [role_row("mid")])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        f"unreported write privileges of writer on {MART}; "
        f"ownership of {MART} for SET ROLE writer; "
        f"schema ownership of {MART} for SET ROLE writer, so read-only access is not established",
    )


def test_a_null_named_privilege_row_stands_for_no_requested_role() -> None:
    """A per-mart row whose name is NULL stands for no requested role, however
    `str(None)` spells it: the literal role name "None" must not take such a row's
    answers as its own."""
    engine = role_engine()
    reachable(engine, [{"name": "None", "superuser": False}])
    row = role_row("None")
    row["name"] = None
    reachable_on(engine, MART, ["None"], [row])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        f"unreported write privileges of None on {MART}; "
        f"ownership of {MART} for SET ROLE None; "
        f"schema ownership of {MART} for SET ROLE None, so read-only access is not established",
    )


@pytest.mark.parametrize(
    ("field", "level", "privilege"),
    [
        ("insert", "table", "INSERT"),
        ("update", "table", "UPDATE"),
        ("delete", "table", "DELETE"),
        ("truncate", "table", "TRUNCATE"),
        ("column_insert", "column", "INSERT"),
        ("column_update", "column", "UPDATE"),
    ],
)
def test_a_null_privilege_field_of_a_reachable_role_never_establishes_read_only(
    field: str, level: str, privilege: str
) -> None:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    row = role_row("writer")
    row[field] = None
    reachable_on(engine, MART, ["writer"], [row])
    scope = pg_questions()

    assert_blocks(
        engine,
        scope,
        with_runtime(scope),
        f"unreported {level} {privilege} privileges of writer on {MART}, "
        "so read-only access is not established",
    )


def test_a_proven_write_keeps_its_evidence_over_an_unanswered_reachability() -> None:
    """`role_engine(update=True)` proves the current role's own column write."""
    engine = role_engine(update=True)
    engine = RefusingEngine(engine, reach_sql(), sqlstate="42501")
    scope = pg_questions()

    assert_blocks(engine, scope, with_runtime(scope), f"granted UPDATE on columns of {MART}")


def test_a_proven_write_of_a_reachable_role_keeps_its_evidence_over_an_unanswered_one() -> None:
    engine = role_engine()
    reachable(
        engine,
        [{"name": "writer", "superuser": False}, {"name": None, "superuser": False}],
    )
    reachable_on(engine, MART, ["writer"], [role_row("writer", insert=True)])
    scope = pg_questions()

    assert_blocks(
        engine, scope, with_runtime(scope), f"granted INSERT on {MART} through SET ROLE writer"
    )


# --- exact names --------------------------------------------------------------------


def test_a_role_name_with_a_quote_is_doubled_in_the_literal_and_matched_exactly() -> None:
    engine = role_engine()
    reachable(engine, [{"name": "odd'name", "superuser": False}])
    reachable_on(engine, MART, ["odd'name"], [role_row("odd'name", insert=True)])
    scope = pg_questions()

    facts = assert_blocks(
        engine, scope, with_runtime(scope), f"granted INSERT on {MART} through SET ROLE odd'name"
    )
    assert facts.role.read_only is False
    assert reachable_sql(MART, ["odd'name"]) in engine.asked


def test_a_role_name_with_leading_spaces_is_kept_exactly() -> None:
    engine = role_engine()
    reachable(engine, [{"name": " writer", "superuser": False}])
    reachable_on(engine, MART, [" writer"], [role_row(" writer", insert=True)])
    scope = pg_questions()

    assert_blocks(
        engine, scope, with_runtime(scope), f"granted INSERT on {MART} through SET ROLE  writer"
    )
    assert engine.asked.count(reachable_sql(MART, [" writer"])) == 1


# --- the short-cuts ask no reachability question ------------------------------------


def _superuser_identity() -> FakeEngine:
    engine = role_engine(update=True)
    engine.answers["current_user AS name"] = [{"name": "postgres", "superuser": True}]
    return engine


def _refused_identity() -> RefusingEngine:
    return RefusingEngine(role_engine(), "current_user AS name", sqlstate="42501")


def _empty_identity() -> FakeEngine:
    engine = role_engine()
    engine.answers["current_user AS name"] = []
    return engine


def _partial_identity() -> FakeEngine:
    engine = role_engine()
    engine.answers["current_user AS name"] = [{"name": "gate_ro", "superuser": False}]
    return engine


@pytest.mark.parametrize(
    ("make_engine", "expected"),
    [
        pytest.param(
            _superuser_identity,
            RoleFacts(
                name="postgres",
                read_only=False,
                evidence="a superuser, whose writes no privilege check can stop",
                bypasses_row_security=True,
            ),
            id="superuser-identity",
        ),
        pytest.param(
            _refused_identity,
            RoleFacts(
                name="unknown",
                read_only=False,
                evidence=(
                    "unreported role identity, superuser status and BYPASSRLS, "
                    "so read-only access is not established"
                ),
                bypasses_row_security=None,
            ),
            id="refused-identity",
        ),
        pytest.param(
            _empty_identity,
            RoleFacts(
                name="unknown",
                read_only=False,
                evidence=(
                    "unreported role identity, superuser status and BYPASSRLS, "
                    "so read-only access is not established"
                ),
                bypasses_row_security=None,
            ),
            id="empty-identity",
        ),
        pytest.param(
            _partial_identity,
            RoleFacts(
                name="gate_ro",
                read_only=False,
                evidence=(
                    "unreported BYPASSRLS in role identity, so read-only access is not established"
                ),
                bypasses_row_security=None,
            ),
            id="partial-identity",
        ),
    ],
)
def test_the_identity_short_cuts_send_no_reachability_question(
    make_engine: Callable[[], FakeEngine | RefusingEngine], expected: RoleFacts
) -> None:
    engine = make_engine()
    scope = pg_questions()

    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role == expected
    assert reach_sql() not in engine.asked
    assert not any("pg_has_role(session_user" in sql for sql in engine.asked)
    assert not any("has_table_privilege(r.oid" in sql for sql in engine.asked)


# --- nothing is switched or written --------------------------------------------------


def _reachable_writer_engine() -> FakeEngine:
    engine = role_engine()
    reachable(engine, [{"name": "writer", "superuser": False}])
    reachable_on(engine, MART, ["writer"], [role_row("writer", insert=True)])
    return engine


def _reachable_superuser_engine() -> FakeEngine:
    engine = role_engine()
    reachable(engine, [{"name": "su", "superuser": True}])
    return engine


def _refused_reachability_engine() -> RefusingEngine:
    return RefusingEngine(role_engine(), reach_sql(), sqlstate="42501")


@pytest.mark.parametrize(
    "make_engine",
    [
        pytest.param(lambda: role_engine(), id="clean"),
        pytest.param(_reachable_writer_engine, id="reachable-writer"),
        pytest.param(_reachable_superuser_engine, id="reachable-superuser"),
        pytest.param(_refused_reachability_engine, id="refused-reachability"),
        pytest.param(lambda: postgres_engine(), id="plain-fixture"),
    ],
)
def test_nothing_is_switched_or_written(
    make_engine: Callable[[], FakeEngine | RefusingEngine],
) -> None:
    engine = make_engine()

    collect(engine, pg_questions(), engine="greengage", target="stand")

    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    assert not any(sql.lstrip().upper().startswith(("SET", "RESET")) for sql in engine.asked)
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql in engine.asked)


# --- ClickHouse ignores SET ROLE reachability ----------------------------------------


@pytest.mark.parametrize("profile", [True, False])
def test_clickhouse_collection_ignores_set_role_reachability(profile: bool) -> None:
    engine = (
        clickhouse_engine()
        if profile
        else clickhouse_engine(
            **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": "SELECT"}]}
        )
    )
    scope = questions()

    facts = collect(engine, scope, engine="clickhouse", target="stand")

    assert facts.role == RoleFacts(
        name="gate_ro",
        read_only=True,
        evidence=(
            "readonly=1, so the session refuses writes whatever it is granted"
            if profile
            else "readonly=0, no write access type granted, and SET ROLE reaches no other role"
        ),
    )
    assert not any("session_user" in sql for sql in engine.asked)
    assert not any("pg_roles r" in sql for sql in engine.asked)


# --- the public gate -----------------------------------------------------------------


def test_a_reachable_writer_blocks_dwh_access_at_the_gate(tmp_path: Path) -> None:
    engine = _reachable_writer_engine()
    facts = collect(engine, pg_questions(), engine="greengage", target="stand")
    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "greengage"

    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


def test_the_clean_control_keeps_dwh_access_read_only(tmp_path: Path) -> None:
    engine = role_engine()
    facts = collect(engine, pg_questions(), engine="greengage", target="stand")
    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "greengage"

    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.PASS
    assert summary_of(report, "dwh.access") == "access.read_only"
