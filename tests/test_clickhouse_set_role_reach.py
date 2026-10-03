"""T-38: a ClickHouse role the session can switch on cannot hide a write grant.

ClickHouse 24.8.14.39 (live, throwaway server, 2026-10-03): a user granted `SELECT` and
the role `writer`, which holds `INSERT`, with `SET DEFAULT ROLE NONE` starts with an
empty `system.enabled_roles`, so the grants question names only the user — while
`SET ROLE writer` (or the HTTP parameter `role=writer`) writes. A switch also enables
every role granted to the switched-on role, however deep, and it does not move
`readonly`. So once the own grants come back without a write access type, the collector
asks `system.role_grants` which roles the session could still enable and `system.grants`
what they hold; it never switches and never writes. A role name is an arbitrary string,
and a literal that leaves its backslash alone names another role or none: names are
written with every backslash and every single quote doubled.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from auto_bi.semantic.metric_contract import MetricBudget
from auto_bi.verification.environment import (
    EnvironmentQuestions,
    ExecutionFacts,
    PlanQuestion,
    check_access,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import Severity
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, probing, status_of
from tests.test_environment_probe import (
    WRITE_STATEMENT,
    FakeEngine,
    RefusingEngine,
    clickhouse_engine,
    questions,
)

MART = "dm.sales_daily"

#: The reach question, pinned by its full text: `FakeEngine` answers with the longest
#: marker contained in the SQL, and the grants question of the reached roles below
#: mentions `system.grants` too.
REACH_SQL = (
    "SELECT user_name, role_name, granted_role_name FROM system.role_grants "
    "WHERE user_name = currentUser() OR role_name IS NOT NULL"
)


def ch_literal(value: str) -> str:
    """How the probe must spell a ClickHouse role name: backslashes doubled first."""
    escaped = value.replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def reached_grants_sql(roles: tuple[str, ...] | list[str]) -> str:
    """The one question naming the reached roles, in the sorted order the probe chose."""
    listed = ", ".join(ch_literal(role) for role in roles)
    return (
        "SELECT role_name, access_type, is_partial_revoke FROM system.grants "
        f"WHERE role_name IN ({listed})"
    )


def granted_to_user(role: str) -> dict[str, Any]:
    """A `system.role_grants` row: the role granted directly to the session user."""
    return {"user_name": "gate_ro", "granted_role_name": role}


def granted_to_role(holder: str, role: str) -> dict[str, Any]:
    """A `system.role_grants` row: `role` granted to `holder`, enabled together with it."""
    return {"role_name": holder, "granted_role_name": role}


def grant_row(role: str, access: str, *, partial: int = 0) -> dict[str, Any]:
    """A reached-role grants row: a plain grant unless a test makes it a partial revoke."""
    return {"role_name": role, "access_type": access, "is_partial_revoke": partial}


def stand(
    reach: list[dict[str, Any]] | None = None,
    reached: dict[tuple[str, ...], list[dict[str, Any]]] | None = None,
    **overrides: Any,
) -> FakeEngine:
    """The stand session: `readonly=0`, own grants holding only SELECT.

    `reached` is keyed by the role names the probe's own sorting must produce, so a
    grants question naming anything else stays unanswered and its test fails.
    """
    answers: dict[str, Any] = {
        "system.settings": [{"value": "0"}],
        "system.grants": [{"access_type": "SELECT"}],
    }
    if reach is not None:
        answers[REACH_SQL] = reach
    for names, rows in (reached or {}).items():
        answers[reached_grants_sql(names)] = rows
    answers.update(overrides)
    return clickhouse_engine(**answers)


def with_runtime(scope: EnvironmentQuestions) -> None:
    """An eligible exact runtime probe, as the neighbouring runtime tests attach one."""
    scope.plans.append(
        PlanQuestion(
            metric_ref=scope.tables[0].metric_refs[0],
            table=MART,
            sql=f"SELECT sum(revenue) FROM {MART}",
            dialect="clickhouse",
            budgets=MetricBudget(timeout_ms=100),
        )
    )


def cost_recorder() -> tuple[Callable[[PlanQuestion], ExecutionFacts], list[str]]:
    calls: list[str] = []

    def run_cost(question: PlanQuestion) -> ExecutionFacts:
        calls.append(question.sql)
        return ExecutionFacts(
            metric_ref=question.metric_ref, elapsed_ms=1, evidence="exact SELECT completed"
        )

    return run_cost, calls


# --- a granted role that is not enabled blocks --------------------------------------


def test_a_granted_role_that_is_not_enabled_blocks(tmp_path: Path) -> None:
    """The shape ClickHouse 24.8.14.39 answered live: empty `system.enabled_roles`,
    own grants naming only `SELECT`, and a writable role one `SET ROLE` away."""
    engine = stand(
        reach=[granted_to_user("writer")],
        reached={("writer",): [grant_row("writer", "INSERT")]},
    )
    scope = questions()
    with_runtime(scope)
    run_cost, calls = cost_recorder()

    facts = collect(engine, scope, engine="clickhouse", target="stand", run_cost=run_cost)

    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, SET ROLE reaches INSERT granted to writer"
    assert [(item.code, item.severity) for item in check_access(scope, facts)] == [
        ("access.role_writable", Severity.BLOCK)
    ]
    assert calls == []
    assert facts.executions == []
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK


def test_a_write_grant_one_role_deeper_blocks() -> None:
    """`SET ROLE` enables with the role every role granted to it, however deep."""
    engine = stand(
        reach=[granted_to_user("mid"), granted_to_role("mid", "deepwriter")],
        reached={("deepwriter", "mid"): [grant_row("deepwriter", "ALTER UPDATE")]},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, SET ROLE reaches ALTER UPDATE granted to deepwriter"
    assert reached_grants_sql(["deepwriter", "mid"]) in engine.asked


# --- false-reject controls -----------------------------------------------------------


def test_a_role_no_grant_leads_to_is_not_reached(tmp_path: Path) -> None:
    """The reach question also carries other principals' role-to-role rows; a row
    nothing granted to the session leads to reaches nothing and asks about nothing."""
    engine = stand(
        reach=[granted_to_user("reader2"), granted_to_role("mid", "deepwriter")],
        reached={("reader2",): [grant_row("reader2", "SELECT")]},
    )
    scope = questions()
    with_runtime(scope)
    run_cost, calls = cost_recorder()

    facts = collect(engine, scope, engine="clickhouse", target="stand", run_cost=run_cost)

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, also not to the roles SET ROLE reaches (reader2)"
    )
    assert check_access(scope, facts) == []
    assert len(calls) == 1  # an eligible exact runtime probe still runs
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.PASS


def test_an_empty_reach_answer_stays_read_only() -> None:
    engine = stand(reach=[])

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, and SET ROLE reaches no other role"
    )
    assert engine.asked.count(REACH_SQL) == 1
    assert not any("WHERE role_name IN" in sql for sql in engine.asked)


def test_the_only_granted_role_already_enabled_asks_no_further_question() -> None:
    """The grants question already named the enabled role, so the reach excludes it."""
    engine = stand(
        reach=[granted_to_user("writer")],
        **{"system.enabled_roles": [{"role_name": "writer"}]},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, and SET ROLE reaches no other role"
    )
    assert engine.asked.count(REACH_SQL) == 1
    assert not any("WHERE role_name IN" in sql for sql in engine.asked)


def test_an_enabled_role_is_not_asked_about_twice() -> None:
    engine = stand(
        reach=[granted_to_user("analyst_role"), granted_to_user("writer")],
        reached={("writer",): [grant_row("writer", "SELECT")]},
        **{"system.enabled_roles": [{"role_name": "analyst_role"}]},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, also not to the roles SET ROLE reaches (writer)"
    )
    assert reached_grants_sql(["writer"]) in engine.asked


@pytest.mark.parametrize("positive", [False, True])
def test_a_partial_revoke_on_a_reached_role_is_not_a_write_grant(positive: bool) -> None:
    rows = [grant_row("writer", "INSERT", partial=1)]
    if positive:
        rows.append(grant_row("writer", "INSERT"))
    engine = stand(reach=[granted_to_user("writer")], reached={("writer",): rows})

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is not positive
    if not positive:
        assert facts.role.evidence == (
            "readonly=0, no write access type granted, also not to the roles SET ROLE "
            "reaches (writer)"
        )


def test_roles_granted_to_each_other_are_read_once() -> None:
    engine = stand(
        reach=[granted_to_user("a"), granted_to_role("a", "b"), granted_to_role("b", "a")],
        reached={("a", "b"): []},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, also not to the roles SET ROLE reaches (a, b)"
    )
    assert engine.asked.count(reached_grants_sql(["a", "b"])) == 1


# --- unanswered reach or reached-role questions --------------------------------------


def assert_reach_gap_blocks(tmp_path: Path, engine: FakeEngine | RefusingEngine) -> None:
    scope = questions()
    with_runtime(scope)
    run_cost, calls = cost_recorder()

    facts = collect(engine, scope, engine="clickhouse", target="stand", run_cost=run_cost)

    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "readonly=0, unreported or incomplete: system.role_grants, "
        "so read-only access is not established"
    )
    assert "no write" not in facts.role.evidence
    assert calls == []
    assert [item.code for item in check_access(scope, facts)] == ["access.role_writable"]
    report = Case(tmp_path).run(probe=probing(facts))
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK


def test_a_refused_reach_question_never_establishes_read_only(tmp_path: Path) -> None:
    assert_reach_gap_blocks(tmp_path, RefusingEngine(stand(), "system.role_grants"))


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([{}], id="empty-row"),
        pytest.param([{"user_name": "gate_ro", "granted_role_name": None}], id="null-granted"),
        pytest.param([{"user_name": "gate_ro", "granted_role_name": ""}], id="empty-granted"),
        pytest.param([{"granted_role_name": "writer"}], id="neither-user-nor-role"),
    ],
)
def test_an_incomplete_reach_row_never_establishes_read_only(
    tmp_path: Path, rows: list[dict[str, Any]]
) -> None:
    assert_reach_gap_blocks(tmp_path, stand(reach=rows))


def test_a_complete_row_of_an_incomplete_reach_answer_still_finds_the_writer() -> None:
    engine = stand(
        reach=[granted_to_user("writer"), {}],
        reached={("writer",): [grant_row("writer", "INSERT")]},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, SET ROLE reaches INSERT granted to writer"


def assert_reached_grants_gap_blocks(engine: FakeEngine | RefusingEngine) -> None:
    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "readonly=0, unreported or incomplete: grants of the roles SET ROLE reaches (writer), "
        "so read-only access is not established"
    )
    assert "no write" not in facts.role.evidence


def test_a_refused_reached_roles_grants_question_never_establishes_read_only() -> None:
    inner = stand(reach=[granted_to_user("writer")])
    assert_reached_grants_gap_blocks(RefusingEngine(inner, reached_grants_sql(["writer"])))


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param([{"role_name": "writer", "access_type": None}], id="null-access"),
        pytest.param([{"role_name": None, "access_type": "INSERT"}], id="null-role"),
        pytest.param([{}], id="empty-row"),
    ],
)
def test_an_incomplete_reached_roles_grants_row_never_establishes_read_only(
    rows: list[dict[str, Any]],
) -> None:
    engine = stand(reach=[granted_to_user("writer")], reached={("writer",): rows})
    assert_reached_grants_gap_blocks(engine)


def test_a_complete_write_row_beside_an_incomplete_one_keeps_positive_evidence() -> None:
    engine = stand(
        reach=[granted_to_user("writer")],
        reached={
            ("writer",): [
                {"role_name": "writer", "access_type": None},
                grant_row("writer", "INSERT"),
            ]
        },
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, SET ROLE reaches INSERT granted to writer"


# --- the decided cases ask neither question ------------------------------------------


@pytest.mark.parametrize(
    ("engine", "evidence"),
    [
        pytest.param(
            clickhouse_engine(),
            "readonly=1, so the session refuses writes whatever it is granted",
            id="readonly-1",
        ),
        pytest.param(
            clickhouse_engine(**{"system.settings": [{"value": "2"}]}),
            "readonly=2, so the session refuses writes whatever it is granted",
            id="readonly-2",
        ),
        pytest.param(
            RefusingEngine(
                clickhouse_engine(**{"system.settings": [{"value": "0"}]}), "system.grants"
            ),
            (
                "readonly=0 and system.grants is out of reach, so nothing establishes "
                "that this session cannot write"
            ),
            id="grants-refused",
        ),
        pytest.param(
            RefusingEngine(stand(), "system.enabled_roles"),
            (
                "readonly=0 and system.enabled_roles is unreported or incomplete, "
                "so read-only access is not established"
            ),
            id="enabled-roles-refused",
        ),
        pytest.param(
            clickhouse_engine(**{"system.settings": [{"value": "0"}]}),
            "readonly=0, granted INSERT",
            id="own-write-grant",
        ),
    ],
)
def test_the_decided_cases_send_neither_new_question(
    engine: FakeEngine | RefusingEngine, evidence: str
) -> None:
    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.evidence == evidence
    assert not any("system.role_grants" in sql for sql in engine.asked)
    assert sum("system.grants" in sql for sql in engine.asked) <= 1


# --- exact names and unchanged session facts -----------------------------------------


def test_an_unset_readonly_names_unset_beside_the_reached_writer() -> None:
    engine = stand(
        reach=[granted_to_user("writer")],
        reached={("writer",): [grant_row("writer", "INSERT")]},
        **{"system.settings": []},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.evidence.startswith("readonly=unset, SET ROLE reaches")


def test_exact_reached_role_names_keep_their_spelling() -> None:
    names = ["odd'name", "wri\\ter", " writer"]  # a real backslash in `wri\ter`
    engine = stand(
        reach=[granted_to_user(name) for name in names],
        reached={tuple(sorted(names)): [grant_row(name, "SELECT") for name in sorted(names)]},
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is True
    assert facts.role.evidence == (
        "readonly=0, no write access type granted, also not to the roles SET ROLE "
        "reaches ( writer, odd'name, wri\\ter)"
    )
    grants_sql = next(sql for sql in engine.asked if "WHERE role_name IN" in sql)
    assert "IN (' writer', 'odd''name', 'wri\\\\ter')" in grants_sql


def test_an_enabled_role_with_a_backslash_keeps_its_grants_in_view() -> None:
    """The engine answers the grants question by its exact text, so a literal that
    leaves the backslash alone (`_literal` on HEAD) names no role and stays red."""
    grants_sql = (
        "SELECT access_type, database, table, column, is_partial_revoke FROM system.grants "
        "WHERE user_name = currentUser() OR role_name IN ('wri\\\\ter')"
    )
    engine = stand(
        **{
            "system.enabled_roles": [{"role_name": "wri\\ter"}],
            grants_sql: [{"access_type": "ALTER UPDATE", "is_partial_revoke": 0}],
        }
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == "readonly=0, granted ALTER UPDATE"
    asked = next(sql for sql in engine.asked if "system.grants" in sql)
    assert "'wri\\\\ter'" in asked  # two backslash characters in the SQL text


def test_reached_roles_change_neither_column_reach_nor_policy_applicability() -> None:
    """The reach asks what a switched-on role could do; what this session may read is
    still answered for its user and its enabled roles only."""
    engine = stand(
        reach=[granted_to_user("writer")],
        reached={
            ("writer",): [
                {
                    **grant_row("writer", "SELECT"),
                    "database": "dm",
                    "table": "sales_daily",
                    "column": "revenue",
                }
            ]
        },
        **{
            "system.grants": [
                {
                    "access_type": "SELECT",
                    "database": "dm",
                    "table": "sales_daily",
                    "column": "date",
                    "is_partial_revoke": 0,
                }
            ],
            "system.row_policies": [
                {
                    "short_name": "writer_rows",
                    "select_filter": "store_id = 1",
                    "is_restrictive": 1,
                    "apply_to_all": 0,
                    "apply_to_list": ["writer"],
                    "apply_to_except": [],
                }
            ],
        },
    )

    facts = collect(
        engine,
        questions({"date": "Date", "revenue": "Decimal(18,2)"}),
        engine="clickhouse",
        target="stand",
    )

    assert facts.tables[0].column_reads == {"date": True, "revenue": False}
    policies = facts.tables[0].row_policies or []
    assert [policy.applies_to_session for policy in policies] == [False]


# --- nothing is switched or written --------------------------------------------------


@pytest.mark.parametrize(
    "make_engine",
    [
        pytest.param(
            lambda: stand(
                reach=[granted_to_user("writer")],
                reached={("writer",): [grant_row("writer", "INSERT")]},
            ),
            id="reached-writer",
        ),
        pytest.param(lambda: stand(reach=[]), id="empty-reach"),
        pytest.param(lambda: RefusingEngine(stand(), "system.role_grants"), id="refused-reach"),
        pytest.param(
            lambda: RefusingEngine(
                stand(reach=[granted_to_user("writer")]), reached_grants_sql(["writer"])
            ),
            id="refused-reached-grants",
        ),
    ],
)
def test_no_question_switches_or_writes(
    make_engine: Callable[[], FakeEngine | RefusingEngine],
) -> None:
    engine = make_engine()

    collect(engine, questions(), engine="clickhouse", target="stand")

    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    assert not any(sql.lstrip().upper().startswith("SET") for sql in engine.asked)
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql in engine.asked)


def test_a_connection_error_on_the_reach_question_propagates() -> None:
    inner = stand()

    def broken(sql: str) -> list[dict[str, Any]]:
        if "system.role_grants" in sql:
            raise ConnectionError("connection lost")
        return inner(sql)

    with pytest.raises(ConnectionError, match="connection lost"):
        collect(broken, questions(), engine="clickhouse", target="stand")


def test_several_write_grants_are_sorted_and_deduplicated() -> None:
    engine = stand(
        reach=[granted_to_user("writer"), granted_to_user("deepwriter")],
        reached={
            ("deepwriter", "writer"): [
                grant_row("writer", "INSERT"),
                grant_row("writer", "INSERT"),  # a duplicate row names one grant
                grant_row("deepwriter", "ALTER UPDATE"),
            ]
        },
    )

    facts = collect(engine, questions(), engine="clickhouse", target="stand")

    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "readonly=0, SET ROLE reaches ALTER UPDATE granted to deepwriter, INSERT granted to writer"
    )
