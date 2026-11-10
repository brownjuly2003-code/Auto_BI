"""Canonical ClickHouse leaf write grants must reach access grading and runtime gating."""

from pathlib import Path

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import ExecutionFacts, PlanQuestion, check_access
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, probing, status_of, summary_of
from tests.test_environment_probe import (
    WRITE_STATEMENT,
    RefusingEngine,
    clickhouse_engine,
    questions,
)


@pytest.mark.parametrize(
    "privilege",
    [
        "ALTER UPDATE",
        "ALL",
        "ALTER DELETE",
        "ALTER ADD COLUMN",
        "ALTER DROP COLUMN",
        "ALTER MODIFY COLUMN",
        "ALTER TABLE",
        "CREATE TABLE",
        "CREATE VIEW",
        "CREATE DATABASE",
        "DROP TABLE",
        "DROP VIEW",
        "DROP DATABASE",
    ],
)
@pytest.mark.parametrize("via_role", [False, True])
def test_leaf_write_grant_blocks_runtime_and_public_gate(
    tmp_path: Path, privilege: str, via_role: bool
) -> None:
    engine = clickhouse_engine(
        **{
            "system.settings": [{"value": "0"}],
            "system.enabled_roles": [{"role_name": "writer"}] if via_role else [],
            "system.grants": [{"access_type": "SELECT"}, {"access_type": privilege}],
        }
    )
    scope = questions()
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
    assert facts.role.read_only is False
    assert facts.role.evidence == f"readonly=0, granted {privilege}"
    assert calls == []
    assert facts.executions == []
    assert [f.code for f in check_access(scope, facts)] == ["access.role_writable"]
    assert ("'writer'" in next(s for s in engine.asked if "system.grants" in s)) is via_role
    assert not any(WRITE_STATEMENT.match(s) for s in engine.asked)
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


@pytest.mark.parametrize("readonly", ["1", "2"])
def test_profile_still_overrides_leaf_write_grants(readonly: str) -> None:
    engine = clickhouse_engine(
        **{
            "system.settings": [{"value": readonly}],
            "system.grants": [{"access_type": "ALTER UPDATE"}],
        }
    )
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is True
    assert facts.role.evidence == (
        f"readonly={readonly}, so the session refuses writes whatever it is granted"
    )


@pytest.mark.parametrize(
    "privilege", ["SELECT", "SHOW TABLES", "SHOW CREATE", "ALTERED", "SYSTEM DROP CACHE"]
)
def test_nonfamily_access_types_are_not_leaf_writes(privilege: str) -> None:
    engine = clickhouse_engine(
        **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": privilege}]}
    )
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is True


@pytest.mark.parametrize("positive", [False, True])
def test_partial_revoke_is_not_a_positive_leaf_grant(positive: bool) -> None:
    grants = [{"access_type": "ALTER DELETE", "is_partial_revoke": 1}]
    if positive:
        grants.append({"access_type": "ALTER DELETE", "is_partial_revoke": 0})
    engine = clickhouse_engine(**{"system.settings": [{"value": "0"}], "system.grants": grants})
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is not positive
    if positive:
        assert facts.role.evidence == "readonly=0, granted ALTER DELETE"


def test_known_leaf_grant_keeps_priority_when_roles_are_unknown() -> None:
    engine = clickhouse_engine(
        **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": "DROP TABLE"}]}
    )
    facts = collect(
        RefusingEngine(engine, "system.enabled_roles"),
        questions(),
        engine="clickhouse",
        target="stand",
    )
    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, granted DROP TABLE"


@pytest.mark.parametrize(
    "privilege", ["INSERT", "ALTER", "CREATE", "DROP", "TRUNCATE", "OPTIMIZE", "UNDROP TABLE"]
)
def test_existing_exact_write_grants_keep_their_rules(privilege: str) -> None:
    engine = clickhouse_engine(
        **{"system.settings": [{"value": "0"}], "system.grants": [{"access_type": privilege}]}
    )
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is False
    assert facts.role.evidence == f"readonly=0, granted {privilege}"


def test_unset_profile_and_mixed_grants_keep_exact_sorted_evidence() -> None:
    engine = clickhouse_engine(
        **{
            "system.settings": [],
            "system.grants": [
                {"access_type": p}
                for p in (
                    "DROP ROLE",
                    "SELECT",
                    "ALTER USER",
                    "CREATE NAMED COLLECTION",
                    "ALTER USER",
                )
            ],
        }
    )
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "readonly=unset, granted ALTER USER, CREATE NAMED COLLECTION, DROP ROLE"
    )
