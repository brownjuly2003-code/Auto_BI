"""Incomplete enabled roles cannot establish effective access or policy applicability."""

from pathlib import Path

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import (
    ExecutionFacts,
    PlanQuestion,
    check_access,
    check_physical,
    check_security,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, probing, status_of
from tests.test_environment_probe import (
    WRITE_STATEMENT,
    RefusingEngine,
    clickhouse_engine,
    questions,
)


@pytest.mark.parametrize("answer", ["refused", "missing", "null", "empty", "mixed"])
@pytest.mark.parametrize("readonly", ["0", "1", "2", None])
def test_incomplete_roles_reach_public_gate_and_runtime(
    tmp_path: Path, answer: str, readonly: str | None
) -> None:
    rows = {
        "refused": [],
        "missing": [{}],
        "null": [{"role_name": None}],
        "empty": [{"role_name": ""}],
        "mixed": [{"role_name": "analyst_role"}, {}],
    }[answer]
    inner = clickhouse_engine(
        **{
            "system.settings": [{"value": readonly}] if readonly is not None else [],
            "system.enabled_roles": rows,
            "system.grants": [
                {
                    "access_type": "SELECT",
                    "column": "date",
                    "database": "dm",
                    "table": "sales_daily",
                }
            ],
            "system.row_policies": [
                {
                    "short_name": "role_filter",
                    "select_filter": "region=1",
                    "apply_to_list": ["hidden_role"],
                }
            ],
        }
    )
    engine = RefusingEngine(inner, "system.enabled_roles") if answer == "refused" else inner
    scope = questions({"date": "Date", "revenue": "Decimal(18,2)"})
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table="dm.sales_daily",
            sql="SELECT sum(revenue) FROM dm.sales_daily",
            dialect="clickhouse",
            budgets=MetricBudget(timeout_ms=100),
        )
    )
    calls = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        calls.append(question.sql)
        return ExecutionFacts(metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT")

    facts = collect(engine, scope, engine="clickhouse", target="stand", run_cost=run_cost)
    protected = readonly in {"1", "2"}
    assert facts.role.read_only is protected
    assert len(calls) == int(protected)
    assert facts.tables[0].column_reads is None
    assert facts.tables[0].row_policies is None
    assert [f.code for f in check_security(scope, facts)] == ["security.not_observed"]
    assert [f.code for f in check_access(scope, facts)] == (
        [] if protected else ["access.role_writable"]
    )
    if not protected:
        assert "system.enabled_roles" in facts.role.evidence
        assert "no write" not in facts.role.evidence
    grants_sql = next(sql for sql in inner.asked if "system.grants" in sql)
    assert ("'analyst_role'" in grants_sql) is (answer == "mixed")
    assert not any(WRITE_STATEMENT.match(sql) for sql in inner.asked)
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is (CheckStatus.PASS if protected else CheckStatus.BLOCK)
    assert status_of(report, "dwh.security") is CheckStatus.REVIEW


def test_incomplete_roles_preserve_proven_write_grant() -> None:
    inner = clickhouse_engine(
        **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": "INSERT"}]}
    )
    facts = collect(
        RefusingEngine(inner, "system.enabled_roles"),
        questions(),
        engine="clickhouse",
        target="stand",
    )
    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, granted INSERT"


@pytest.mark.parametrize("roles", [[], [{"role_name": "analyst_role"}], [{"role_name": " "}]])
def test_complete_roles_keep_grants_and_policy_facts(roles: list[dict[str, str]]) -> None:
    engine = clickhouse_engine(
        **{
            "system.settings": [{"value": "0"}],
            "system.enabled_roles": roles,
            "system.grants": [{"access_type": "SELECT"}],
        }
    )
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is True
    assert facts.tables[0].column_reads == {"date": True}
    assert facts.tables[0].row_policies == []
    if roles:
        assert repr(roles[0]["role_name"]) in next(s for s in engine.asked if "system.grants" in s)


def test_unknown_reach_does_not_invent_absence_or_unenforced_classification() -> None:
    inner = clickhouse_engine(**{"system.columns": [{"name": "date", "type": "Date"}]})
    scope = questions({"date": "Date", "revenue": "Decimal(18,2)"})
    scope.tables[0].sensitive_columns = ["revenue"]
    facts = collect(
        RefusingEngine(inner, "system.enabled_roles"), scope, engine="clickhouse", target="stand"
    )
    assert facts.tables[0].row_policies == []
    assert [f.code for f in check_security(scope, facts)] == ["security.not_observed"]
    assert check_physical(scope, facts) == []


def test_unknown_reach_still_reports_observed_type_drift() -> None:
    inner = clickhouse_engine()
    scope = questions({"date": "DateTime"})
    facts = collect(
        RefusingEngine(inner, "system.enabled_roles"), scope, engine="clickhouse", target="stand"
    )
    assert [f.code for f in check_physical(scope, facts)] == ["physical.type_drift"]


@pytest.mark.parametrize("engine_name", ["clickhouse", "postgres", "greengage"])
@pytest.mark.parametrize("known_reach", [True, False])
def test_absence_requires_known_clickhouse_reach(engine_name: str, known_reach: bool) -> None:
    scope = questions({"date": "Date", "revenue": "Decimal(18,2)"})
    engine = clickhouse_engine(**{"system.columns": [{"name": "date", "type": "Date"}]})
    facts = collect(engine, scope, engine="clickhouse", target="stand")
    facts.engine = engine_name
    facts.tables[0].column_reads = {"date": True, "revenue": True} if known_reach else None
    assert [f.code for f in check_physical(scope, facts)] == (
        [] if engine_name == "clickhouse" and not known_reach else ["physical.column_absent"]
    )


def test_unknown_column_reach_cannot_establish_unenforced_classification() -> None:
    scope = questions()
    scope.tables[0].sensitive_columns = ["date"]
    facts = collect(clickhouse_engine(), scope, engine="clickhouse", target="stand")
    facts.tables[0].column_reads = None
    assert [f.code for f in check_security(scope, facts)] == ["security.not_observed"]


def test_role_catalog_connection_error_propagates() -> None:
    inner = clickhouse_engine()

    def broken(sql: str) -> list[dict[str, object]]:
        if "system.enabled_roles" in sql:
            raise ConnectionError("connection lost")
        return inner(sql)

    with pytest.raises(ConnectionError, match="connection lost"):
        collect(broken, questions(), engine="clickhouse", target="stand")
