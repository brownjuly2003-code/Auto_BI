from pathlib import Path

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import ExecutionFacts, PlanQuestion, RoleFacts, check_access
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import Severity
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import (
    Case,
    detail_of,
    probing,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_environment_probe import WRITE_STATEMENT, RefusingEngine
from tests.test_postgres_column_write_privileges import pg_questions, role_engine


@pytest.mark.parametrize("answer", ["refused", "empty"])
@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
@pytest.mark.parametrize("prepared_write_grant", [False, True])
def test_unreported_postgres_identity_fails_closed_before_grants_or_cost(
    tmp_path: Path, answer: str, engine_name: str, prepared_write_grant: bool
) -> None:
    engine = role_engine(update=prepared_write_grant)
    if answer == "refused":
        engine = RefusingEngine(engine, "current_user AS name", sqlstate="42501")
    else:
        engine.answers["current_user AS name"] = []

    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    cost_calls = 0

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        nonlocal cost_calls
        cost_calls += 1
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(
        engine,
        scope,
        engine=engine_name,
        target="stand",
        run_cost=run_cost,
    )

    assert facts.role.name == "unknown"
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is None
    assert "identity" in facts.role.evidence
    assert "superuser" in facts.role.evidence
    assert "BYPASSRLS" in facts.role.evidence
    assert "read-only" in facts.role.evidence
    assert "not established" in facts.role.evidence
    findings = check_access(scope, facts)
    assert [(item.code, item.severity) for item in findings] == [
        ("access.role_writable", Severity.BLOCK)
    ]
    assert "whose read-only access is not established" in findings[0].detail
    assert "which can write" not in findings[0].detail
    assert cost_calls == 0
    assert facts.executions == []
    assert not any("has_table_privilege" in sql_text for sql_text in engine.asked)
    assert not any(
        "has_any_column_privilege" in sql_text and "'SELECT'" not in sql_text
        for sql_text in engine.asked
    )
    assert not any(WRITE_STATEMENT.match(sql_text) for sql_text in engine.asked)

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"
    assert "whose read-only access is not established" in detail_of(report, "dwh.access")


@pytest.mark.parametrize(
    "engine_name",
    ["greenplum", "greengage", "postgres", "postgresql"],
)
@pytest.mark.parametrize(
    (
        "identity",
        "prepared_write_grant",
        "expected_name",
        "expected_bypass",
        "missing_fields",
    ),
    [
        pytest.param(
            {},
            False,
            "unknown",
            None,
            ("name", "superuser", "BYPASSRLS"),
            id="all-fields-absent",
        ),
        pytest.param(
            {"superuser": False, "bypassrls": False},
            False,
            "unknown",
            False,
            ("name",),
            id="missing-name-with-known-false-flags",
        ),
        pytest.param(
            {"name": None, "superuser": False, "bypassrls": False},
            True,
            "unknown",
            False,
            ("name",),
            id="null-name-with-positive-grant",
        ),
        pytest.param(
            {"name": "", "superuser": False, "bypassrls": False},
            False,
            "unknown",
            False,
            ("name",),
            id="empty-name",
        ),
        pytest.param(
            {"name": "gate_ro", "bypassrls": False},
            True,
            "gate_ro",
            None,
            ("superuser",),
            id="missing-superuser-with-positive-grant",
        ),
        pytest.param(
            {"name": "gate_ro", "superuser": None, "bypassrls": False},
            True,
            "gate_ro",
            None,
            ("superuser",),
            id="null-superuser",
        ),
        pytest.param(
            {"name": "gate_ro", "superuser": False, "bypassrls": None},
            False,
            "gate_ro",
            None,
            ("BYPASSRLS",),
            id="null-bypassrls",
        ),
        pytest.param(
            {"name": "gate_ro", "superuser": False},
            False,
            "gate_ro",
            None,
            ("BYPASSRLS",),
            id="missing-bypassrls-with-known-false-superuser",
        ),
        pytest.param(
            {"name": "gate_ro", "bypassrls": True},
            True,
            "gate_ro",
            True,
            ("superuser",),
            id="missing-superuser-with-proven-bypassrls",
        ),
        pytest.param(
            {"name": "gate_ro", "superuser": None, "bypassrls": True},
            True,
            "gate_ro",
            True,
            ("superuser",),
            id="null-superuser-with-proven-bypassrls",
        ),
    ],
)
def test_partial_postgres_identity_fails_closed_before_grants_or_cost(
    tmp_path: Path,
    engine_name: str,
    identity: dict[str, object],
    prepared_write_grant: bool,
    expected_name: str,
    expected_bypass: bool | None,
    missing_fields: tuple[str, ...],
) -> None:
    engine = role_engine(update=prepared_write_grant)
    engine.answers["current_user AS name"] = [identity]
    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    cost_calls = 0

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        nonlocal cost_calls
        cost_calls += 1
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)

    assert facts.role.name == expected_name
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is expected_bypass
    assert "read-only" in facts.role.evidence
    assert "not established" in facts.role.evidence
    for missing_field in missing_fields:
        assert missing_field in facts.role.evidence
    findings = check_access(scope, facts)
    assert [(item.code, item.severity) for item in findings] == [
        ("access.role_writable", Severity.BLOCK)
    ]
    assert "whose read-only access is not established" in findings[0].detail
    assert "which can write" not in findings[0].detail
    assert cost_calls == 0
    assert facts.executions == []
    assert not any("has_table_privilege" in sql_text for sql_text in engine.asked)
    assert not any(
        "has_any_column_privilege" in sql_text and "'SELECT'" not in sql_text
        for sql_text in engine.asked
    )
    assert not any(WRITE_STATEMENT.match(sql_text) for sql_text in engine.asked)

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"
    assert "whose read-only access is not established" in detail_of(report, "dwh.access")


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
@pytest.mark.parametrize("bypassrls", [False, True])
def test_answered_identity_keeps_clean_access_and_runtime(
    tmp_path: Path, engine_name: str, bypassrls: bool
) -> None:
    engine = role_engine()
    engine.answers["current_user AS name"][0]["bypassrls"] = bypassrls
    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    cost_calls = 0

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        nonlocal cost_calls
        cost_calls += 1
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)

    assert facts.role.name == "gate_ro"
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is bypassrls
    assert check_access(scope, facts) == []
    assert cost_calls == 1
    assert engine.asked.count(sql) == 1
    assert len(facts.executions) == 1
    assert not any(WRITE_STATEMENT.match(sql_text) for sql_text in engine.asked)

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.PASS
    assert summary_of(report, "dwh.access") == "access.read_only"


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
@pytest.mark.parametrize(
    "quoted_name",
    [
        pytest.param(" gate_ro ", id="quoted-name"),
        pytest.param("   ", id="whitespace-only-name"),
    ],
)
def test_full_postgres_identity_preserves_exact_quoted_name(
    tmp_path: Path, engine_name: str, quoted_name: str
) -> None:
    engine = role_engine()
    engine.answers["current_user AS name"] = [
        {"name": quoted_name, "superuser": False, "bypassrls": False}
    ]
    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    runtime_sql: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        runtime_sql.append(question.sql)
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)

    assert facts.role.name == quoted_name
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is False
    assert check_access(scope, facts) == []
    assert runtime_sql == [sql]
    assert engine.asked.count(sql) == 1

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.PASS
    assert summary_of(report, "dwh.access") == "access.read_only"


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
@pytest.mark.parametrize(
    "quoted_name",
    [
        pytest.param(" gate_ro ", id="quoted-name"),
        pytest.param("   ", id="whitespace-only-name"),
    ],
)
def test_partial_postgres_identity_preserves_exact_quoted_name(
    tmp_path: Path, engine_name: str, quoted_name: str
) -> None:
    engine = role_engine(update=True)
    engine.answers["current_user AS name"] = [{"name": quoted_name, "superuser": False}]
    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    runtime_sql: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        runtime_sql.append(question.sql)
        engine(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)

    assert facts.role.name == quoted_name
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is None
    assert "BYPASSRLS" in facts.role.evidence
    assert "role name" not in facts.role.evidence
    assert runtime_sql == []
    assert facts.executions == []
    assert sql not in engine.asked
    assert not any("has_table_privilege" in sql_text for sql_text in engine.asked)
    assert not any(
        "has_any_column_privilege" in sql_text and "'SELECT'" not in sql_text
        for sql_text in engine.asked
    )

    findings = check_access(scope, facts)
    assert [(item.code, item.severity, item.subject) for item in findings] == [
        ("access.role_writable", Severity.BLOCK, quoted_name)
    ]

    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    report = Case(tmp_path).with_candidate(model).run(probe=probing(facts))

    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    access = next(check for check in report.checks if check.check_id == "dwh.access")
    assert access.subject_refs == [report.product_ref, quoted_name]


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
@pytest.mark.parametrize(
    ("identity", "expected_name"),
    [
        pytest.param({"superuser": True}, "unknown", id="absent-name"),
        pytest.param(
            {"name": "postgres", "superuser": True, "bypassrls": None},
            "postgres",
            id="null-bypassrls",
        ),
    ],
)
def test_proven_superuser_keeps_facts_when_identity_is_partial(
    engine_name: str, identity: dict[str, object], expected_name: str
) -> None:
    engine = role_engine(update=True)
    engine.answers["current_user AS name"] = [identity]
    scope = pg_questions()
    scope.engines = [engine_name]
    sql = "SELECT sum(revenue) FROM dm.sales_daily"
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=scope.tables[0].table,
            sql=sql,
            dialect="postgres",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    cost_calls = 0

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        nonlocal cost_calls
        cost_calls += 1
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)

    assert facts.role == RoleFacts(
        name=expected_name,
        read_only=False,
        evidence="a superuser, whose writes no privilege check can stop",
        bypasses_row_security=True,
    )
    assert cost_calls == 0
    assert facts.executions == []
    assert not any("has_table_privilege" in sql_text for sql_text in engine.asked)
    assert not any(
        "has_any_column_privilege" in sql_text and "'SELECT'" not in sql_text
        for sql_text in engine.asked
    )
    assert not any(WRITE_STATEMENT.match(sql_text) for sql_text in engine.asked)


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_answered_superuser_keeps_its_facts_without_privilege_questions(engine_name: str) -> None:
    engine = role_engine()
    engine.answers["current_user AS name"] = [{"name": "postgres", "superuser": True}]
    scope = pg_questions()
    scope.engines = [engine_name]
    facts = collect(engine, scope, engine=engine_name, target="stand")

    assert facts.role == RoleFacts(
        name="postgres",
        read_only=False,
        evidence="a superuser, whose writes no privilege check can stop",
        bypasses_row_security=True,
    )
    assert [(item.code, item.severity) for item in check_access(scope, facts)] == [
        ("access.role_writable", Severity.BLOCK)
    ]
    assert not any("has_table_privilege" in sql_text for sql_text in engine.asked)
    assert not any(WRITE_STATEMENT.match(sql_text) for sql_text in engine.asked)


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_identity_connection_failure_remains_a_runtime_fault(engine_name: str) -> None:
    engine = role_engine()

    def disconnected(sql: str) -> list[dict[str, object]]:
        if "current_user AS name" in sql:
            raise ConnectionError("identity query connection lost")
        return engine(sql)

    with pytest.raises(ConnectionError, match="identity query connection lost"):
        collect(disconnected, pg_questions(), engine=engine_name, target="stand")

    assert engine.asked == []
