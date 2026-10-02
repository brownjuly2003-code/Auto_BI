"""Column write grants must reach the role facts and the public release gate."""

from pathlib import Path

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
    clickhouse_engine,
    postgres_engine,
    questions,
)

MART = "dm.sales_daily"
OUTSIDE = "dm.outside_scope"


def table_sql(mart: str) -> str:
    return "SELECT " + ", ".join(
        f"has_table_privilege('{mart}', '{privilege}') AS {privilege.lower()}"
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
    )


def column_sql(mart: str) -> str:
    return "SELECT " + ", ".join(
        f"has_any_column_privilege('{mart}', '{privilege}') AS column_{privilege.lower()}"
        for privilege in ("INSERT", "UPDATE")
    )


def role_engine(*, insert: bool = False, update: bool = False) -> FakeEngine:
    # Longest markers beat the fixture's permissive has_any_column_privilege default.
    return postgres_engine(
        **{
            table_sql(MART): [dict.fromkeys(("insert", "update", "delete", "truncate"), False)],
            column_sql(MART): [{"column_insert": insert, "column_update": update}],
            column_sql(OUTSIDE): [{"column_insert": True, "column_update": True}],
        }
    )


def pg_questions() -> EnvironmentQuestions:
    return questions({"date": "date", "revenue": "numeric(18,2)"}).model_copy(
        update={"engines": ["greengage"]}
    )


def assert_questions(engine: FakeEngine, marts: tuple[str, ...] = (MART,)) -> None:
    table_queries = [sql for sql in engine.asked if "has_table_privilege" in sql]
    column_queries = [
        sql for sql in engine.asked if "has_any_column_privilege" in sql and "'SELECT'" not in sql
    ]
    assert table_queries == [table_sql(mart) for mart in marts]
    assert column_queries == [column_sql(mart) for mart in marts]
    assert not any(OUTSIDE in sql for sql in engine.asked)
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)


def assert_access(scope: EnvironmentQuestions, facts: EnvironmentFacts, writable: bool) -> None:
    findings = check_access(scope, facts)
    assert [(item.code, item.severity) for item in findings] == (
        [("access.role_writable", Severity.BLOCK)] if writable else []
    )


@pytest.mark.parametrize("privilege", ["UPDATE", "INSERT"])
def test_column_only_write_blocks(privilege: str) -> None:
    engine = role_engine(insert=privilege == "INSERT", update=privilege == "UPDATE")
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted {privilege} on columns of {MART}"
    assert_access(scope, facts, True)
    assert_questions(engine)


def test_column_write_question_covers_the_mart_once_not_metric_columns() -> None:
    engine = role_engine(update=True)
    scope = pg_questions()
    assert set(scope.tables[0].columns) == {"date", "revenue"}
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert "UPDATE on columns of dm.sales_daily" in facts.role.evidence
    assert_access(scope, facts, True)
    assert_questions(engine)
    assert engine.asked.count(column_sql(MART)) == 1
    assert not any("has_column_privilege" in sql and "'UPDATE'" in sql for sql in engine.asked)


def test_table_update_is_reported_once_even_with_column_update() -> None:
    engine = role_engine(update=True)
    engine.answers[table_sql(MART)][0]["update"] = True
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted UPDATE on {MART}"
    assert_access(scope, facts, True)
    assert_questions(engine)


def test_superuser_keeps_evidence_and_sends_no_column_write_questions() -> None:
    engine = role_engine(update=True)
    engine.answers["current_user AS name"] = [{"name": "postgres", "superuser": True}]
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role == RoleFacts(
        name="postgres",
        read_only=False,
        evidence="a superuser, whose writes no privilege check can stop",
        bypasses_row_security=True,
    )
    assert_access(scope, facts, True)
    assert_questions(engine, ())


@pytest.mark.parametrize("grants", ["table_select", "column_select", "select_and_references"])
def test_select_only_controls_remain_read_only_and_readable(grants: str) -> None:
    engine = role_engine()
    engine.answers[f"has_table_privilege('{MART}', 'SELECT')"] = [{"ok": grants != "column_select"}]
    engine.answers[f"has_any_column_privilege('{MART}', 'SELECT')"] = [{"ok": True}]
    engine.answers[f"has_any_column_privilege('{MART}', 'REFERENCES')"] = [
        {"ok": grants == "select_and_references"}
    ]
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is True
    assert facts.tables[0].readable is True
    assert "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart" in (
        facts.role.evidence
    )
    assert_access(scope, facts, False)
    assert_questions(engine)
    assert f"SELECT has_any_column_privilege('{MART}', 'SELECT') AS ok" in engine.asked


def test_read_only_evidence_names_table_and_column_write_privileges() -> None:
    engine = role_engine()
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is True
    assert "no INSERT, UPDATE, DELETE or TRUNCATE privilege on any in-scope mart" in (
        facts.role.evidence
    )
    assert "no INSERT or UPDATE privilege on any of their columns" in facts.role.evidence
    assert_access(scope, facts, False)
    assert_questions(engine)


def test_second_in_scope_mart_column_write_blocks_and_outside_is_not_asked() -> None:
    engine = role_engine()
    second = "dm.sales_monthly"
    engine.answers[table_sql(second)] = [
        dict.fromkeys(("insert", "update", "delete", "truncate"), False)
    ]
    engine.answers[column_sql(second)] = [{"column_insert": False, "column_update": True}]
    scope = pg_questions()
    scope.tables.append(TableQuestion(table=second, event_time_column="date"))
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted UPDATE on columns of {second}"
    assert_access(scope, facts, True)
    assert_questions(engine, (MART, second))


@pytest.mark.parametrize("budget", [{"timeout_ms": 100}, {"max_scanned_bytes": 50000}])
@pytest.mark.parametrize("writable", [True, False])
def test_runtime_probe_is_withheld_for_column_write_and_kept_for_clean_role(
    budget: dict[str, int], writable: bool
) -> None:
    engine = role_engine(update=writable)
    scope = pg_questions()
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=MART,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(**budget),
        )
    )

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert facts.role.read_only is not writable
    assert engine.asked.count(sql) == (0 if writable else 1)
    assert len(facts.executions) == (0 if writable else 1)
    assert_access(scope, facts, writable)
    assert_questions(engine)


class RefusedColumnEngine(FakeEngine):
    def __call__(self, sql: str) -> list[dict[str, object]]:
        if sql == column_sql(MART):
            self.asked.append(sql)
            error = PermissionError("column privilege question refused")
            error.sqlstate = "42501"
            raise error
        return super().__call__(sql)


class RefusedTableEngine(FakeEngine):
    def __call__(self, sql: str) -> list[dict[str, object]]:
        if sql == table_sql(MART):
            self.asked.append(sql)
            error = PermissionError("table privilege question refused")
            error.sqlstate = "42501"
            raise error
        return super().__call__(sql)


@pytest.mark.parametrize("table_writable", [False, True])
def test_refused_column_write_question_never_establishes_read_only(
    table_writable: bool,
) -> None:
    engine = RefusedColumnEngine(role_engine().answers)
    engine.answers[table_sql(MART)][0]["update"] = table_writable
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert (
        "UPDATE on dm.sales_daily" in facts.role.evidence
        if table_writable
        else ("unreported column INSERT/UPDATE privileges on dm.sales_daily" in facts.role.evidence)
    )
    assert_access(scope, facts, True)
    assert_questions(engine)


@pytest.mark.parametrize("answer", ["refused", "empty"])
def test_unanswered_column_write_question_blocks_runtime_and_public_gate(
    tmp_path: Path, answer: str
) -> None:
    engine = RefusedColumnEngine(role_engine().answers) if answer == "refused" else role_engine()
    if answer == "empty":
        engine.answers[column_sql(MART)] = []
    scope = pg_questions()
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=MART,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert sql not in engine.asked
    assert facts.executions == []
    assert facts.role.read_only is False
    assert "unreported column INSERT/UPDATE privileges on dm.sales_daily" in facts.role.evidence
    assert "no INSERT" not in facts.role.evidence
    assert_access(scope, facts, True)
    assert_questions(engine)

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "greengage"
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


@pytest.mark.parametrize("answer", ["refused", "empty"])
def test_unanswered_table_write_question_blocks_runtime_and_public_gate(
    tmp_path: Path, answer: str
) -> None:
    engine = RefusedTableEngine(role_engine().answers) if answer == "refused" else role_engine()
    if answer == "empty":
        engine.answers[table_sql(MART)] = []
    scope = pg_questions()
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=MART,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine="greengage", target="stand", run_cost=run_cost)

    assert sql not in engine.asked
    assert facts.executions == []
    assert facts.role.read_only is False
    assert "unreported table INSERT/UPDATE/DELETE/TRUNCATE privileges on dm.sales_daily" in (
        facts.role.evidence
    )
    assert "no INSERT" not in facts.role.evidence
    assert_access(scope, facts, True)
    assert_questions(engine)

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "greengage"
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


@pytest.mark.parametrize("answer", ["refused", "empty"])
@pytest.mark.parametrize("privilege", ["INSERT", "UPDATE"])
def test_unanswered_table_write_question_does_not_hide_column_grant(
    answer: str, privilege: str
) -> None:
    granted = role_engine(insert=privilege == "INSERT", update=privilege == "UPDATE")
    engine = RefusedTableEngine(granted.answers) if answer == "refused" else granted
    if answer == "empty":
        engine.answers[table_sql(MART)] = []
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == f"granted {privilege} on columns of {MART}"
    assert_access(scope, facts, True)
    assert_questions(engine)


@pytest.mark.parametrize("profile", [True, False])
def test_clickhouse_role_facts_and_questions_are_unchanged(profile: bool) -> None:
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
            else "readonly=0, no write access type granted"
        ),
    )
    assert_access(scope, facts, False)
    assert engine.asked == [
        "SELECT currentUser() AS value",
        "SELECT value FROM system.settings WHERE name = 'readonly'",
        "SELECT role_name FROM system.enabled_roles",
        "SELECT access_type, database, table, column, is_partial_revoke FROM system.grants "
        "WHERE user_name = currentUser()",
        "SELECT engine, sorting_key, partition_key FROM system.tables "
        "WHERE database = 'dm' AND name = 'sales_daily'",
        'SELECT count() AS rows_seen, max("date") AS max_event_time FROM "dm"."sales_daily"',
        "SELECT short_name, select_filter, is_restrictive, apply_to_all, apply_to_list, "
        "apply_to_except FROM system.row_policies WHERE database = 'dm' AND table = 'sales_daily'",
        "SELECT name, type FROM system.columns WHERE database = 'dm' AND table = 'sales_daily'",
    ]
    assert not any("has_any_column_privilege" in sql for sql in engine.asked)
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)


@pytest.mark.parametrize("writable", [True, False])
def test_public_gate_blocks_column_update_and_passes_clean_access(
    tmp_path: Path, writable: bool
) -> None:
    engine = role_engine(update=writable)
    scope = pg_questions()
    facts = collect(engine, scope, engine="greengage", target="stand")
    model = semantic_model()
    for table in model.tables:
        table.physical.engine = "greengage"
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert facts.role.read_only is not writable
    assert status_of(report, "dwh.access") is (CheckStatus.BLOCK if writable else CheckStatus.PASS)
    assert summary_of(report, "dwh.access") == (
        "access.role_writable" if writable else "access.read_only"
    )
    assert_questions(engine)
