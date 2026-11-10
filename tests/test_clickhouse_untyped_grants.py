"""T-42: an untyped own-grant row is an incomplete answer, not no grant."""

from pathlib import Path
from typing import Any

import pytest

from auto_bi.verification.environment import check_access, check_physical, check_security
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_clickhouse_set_role_reach import REACH_SQL, cost_recorder, with_runtime
from tests.test_decision_verify import Case, probing, status_of
from tests.test_environment_probe import (
    WRITE_STATEMENT,
    FakeEngine,
    RefusingEngine,
    clickhouse_engine,
    questions,
)

SELECT = {"access_type": "SELECT", "database": "dm", "table": "sales_daily"}
GAP = (
    "readonly=0 and system.grants returned a row with no access type, "
    "so read-only access is not established"
)
READ_ONLY = "readonly=0, no write access type granted, and SET ROLE reaches no other role"


def observe(
    engine: FakeEngine | RefusingEngine,
    *,
    read_only: bool,
    evidence: str,
    reads: dict[str, bool] | None = None,
    security: tuple[str, ...] = ("security.not_observed",),
    physical: tuple[str, ...] = (),
    classified: bool = False,
    no_reach: bool = True,
):
    scope = questions({"date": "Date", "revenue": "Decimal(18,2)"})
    if classified:
        scope.tables[0].sensitive_columns = ["revenue"]
    with_runtime(scope)
    run_cost, calls = cost_recorder()
    facts = collect(engine, scope, engine="clickhouse", target="stand", run_cost=run_cost)
    assert facts.role.read_only is read_only
    assert facts.role.evidence == evidence
    assert facts.tables[0].column_reads == reads
    assert [f.code for f in check_access(scope, facts)] == (
        [] if read_only else ["access.role_writable"]
    )
    assert [f.code for f in check_security(scope, facts)] == list(security)
    assert [f.code for f in check_physical(scope, facts)] == list(physical)
    assert len(calls) == int(read_only)
    assert calls == (["SELECT sum(revenue) FROM dm.sales_daily"] if read_only else [])
    if no_reach:
        assert not any("system.role_grants" in sql for sql in engine.asked)
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    return facts


def stand(rows: list[dict[str, Any]], setting: str = "0") -> FakeEngine:
    return clickhouse_engine(
        **{"system.settings": [{"value": setting}], "system.grants": rows, REACH_SQL: []}
    )


@pytest.mark.parametrize("row", [{}, {"access_type": None}, {"access_type": ""}])
@pytest.mark.parametrize("beside_select", [False, True])
def test_untyped_own_grant_never_establishes_read_only(
    tmp_path: Path, row: dict[str, Any], beside_select: bool
) -> None:
    facts = observe(stand([row, SELECT] if beside_select else [row]), read_only=False, evidence=GAP)
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert status_of(report, "dwh.security") is CheckStatus.REVIEW


def test_unset_readonly_is_named_unset() -> None:
    engine = stand([{}])
    engine.answers["system.settings"] = []
    observe(engine, read_only=False, evidence=GAP.replace("readonly=0", "readonly=unset"))


@pytest.mark.parametrize("setting", ["1", "2"])
def test_readonly_setting_keeps_read_only_with_unknown_column_reach(
    tmp_path: Path, setting: str
) -> None:
    facts = observe(
        stand([SELECT, {}], setting),
        read_only=True,
        evidence=f"readonly={setting}, so the session refuses writes whatever it is granted",
    )
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.PASS
    assert status_of(report, "dwh.security") is CheckStatus.REVIEW


@pytest.mark.parametrize("untyped_first", [False, True])
def test_proven_write_grant_keeps_its_evidence(untyped_first: bool) -> None:
    write = {"access_type": "INSERT"}
    observe(
        stand([{}, write] if untyped_first else [write, {}]),
        read_only=False,
        evidence="readonly=0, granted INSERT",
    )


def test_incomplete_enabled_roles_keep_their_evidence() -> None:
    observe(
        RefusingEngine(stand([{}]), "system.enabled_roles"),
        read_only=False,
        evidence=(
            "readonly=0 and system.enabled_roles is unreported or incomplete, "
            "so read-only access is not established"
        ),
    )


def test_untyped_partial_revoke_is_not_an_answer() -> None:
    observe(
        stand([SELECT, {"access_type": None, "is_partial_revoke": 1}]),
        read_only=False,
        evidence=GAP,
    )


@pytest.mark.parametrize(
    ("rows", "reads", "security"),
    [
        ([], {"date": False, "revenue": False}, ("security.column_unreadable",)),
        ([{**SELECT, "is_partial_revoke": 0}], {"date": True, "revenue": True}, ()),
        ([{**SELECT, "is_partial_revoke": None}], {"date": True, "revenue": True}, ()),
        (
            [SELECT],
            {"date": True, "revenue": True},
            (),
        ),
        (
            [SELECT, {**SELECT, "column": "revenue", "is_partial_revoke": 1}],
            {"date": True, "revenue": False},
            ("security.column_unreadable",),
        ),
    ],
)
def test_typed_answer_is_read_as_before(
    rows: list[dict[str, Any]], reads: dict[str, bool], security: tuple[str, ...]
) -> None:
    engine = stand(rows)
    observe(
        engine, read_only=True, evidence=READ_ONLY, reads=reads, security=security, no_reach=False
    )
    assert engine.asked == [
        "SELECT currentUser() AS value",
        "SELECT value FROM system.settings WHERE name = 'readonly'",
        "SELECT role_name FROM system.enabled_roles",
        "SELECT access_type, database, table, column, is_partial_revoke FROM system.grants "
        "WHERE user_name = currentUser()",
        "SELECT user_name, role_name, granted_role_name FROM system.role_grants "
        "WHERE user_name = currentUser() OR role_name IS NOT NULL",
        "SELECT engine, sorting_key, partition_key FROM system.tables "
        "WHERE database = 'dm' AND name = 'sales_daily'",
        'SELECT count() AS rows_seen, max("date") AS max_event_time FROM "dm"."sales_daily"',
        "SELECT short_name, select_filter, is_restrictive, apply_to_all, apply_to_list, "
        "apply_to_except FROM system.row_policies WHERE database = 'dm' AND table = 'sales_daily'",
        "SELECT name, type FROM system.columns WHERE database = 'dm' AND table = 'sales_daily'",
        "EXPLAIN ESTIMATE SELECT sum(revenue) FROM dm.sales_daily",
    ]


def test_refused_grants_question_keeps_its_evidence() -> None:
    observe(
        RefusingEngine(stand([SELECT]), "system.grants"),
        read_only=False,
        evidence=(
            "readonly=0 and system.grants is out of reach, so nothing establishes "
            "that this session cannot write"
        ),
    )


@pytest.mark.parametrize("column_only", [True, False])
def test_unknown_reach_invents_neither_unreadable_nor_absent_column(column_only: bool) -> None:
    row = {**SELECT, "column": "date"} if column_only else SELECT
    for untyped in [False, True]:
        engine = stand([row, {}] if untyped else [row])
        engine.answers["system.columns"] = [{"name": "date", "type": "Date"}]
        observe(
            engine,
            read_only=not untyped,
            evidence=GAP if untyped else READ_ONLY,
            reads=None if untyped else {"date": True, "revenue": not column_only},
            security=(
                ("security.not_observed",)
                if untyped
                else (
                    ("security.column_unreadable",)
                    if column_only
                    else ("security.unenforced_classification",)
                )
            ),
            physical=() if untyped or column_only else ("physical.column_absent",),
            classified=True,
            no_reach=untyped,
        )


@pytest.mark.parametrize("untyped", [False, True])
def test_row_policy_facts_are_those_of_a_typed_answer(untyped: bool) -> None:
    engine = stand([SELECT, {}] if untyped else [SELECT])
    engine.answers["system.row_policies"] = [
        {
            "short_name": "own_rows",
            "select_filter": "store_id = 1",
            "is_restrictive": 1,
            "apply_to_all": 0,
            "apply_to_list": ["gate_ro"],
            "apply_to_except": [],
        }
    ]
    facts = observe(
        engine,
        read_only=not untyped,
        evidence=GAP if untyped else READ_ONLY,
        reads=None if untyped else {"date": True, "revenue": True},
        security=(
            ("security.not_observed", "security.row_filtered")
            if untyped
            else ("security.row_filtered",)
        ),
        no_reach=untyped,
    )
    assert [policy.model_dump() for policy in facts.tables[0].row_policies or []] == [
        {
            "name": "own_rows",
            "applies_to_session": True,
            "restrictive": True,
            "expression": "store_id = 1",
        }
    ]
