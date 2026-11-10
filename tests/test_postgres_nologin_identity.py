"""T-39: a current role that cannot log in is graded by its privileges.

A session parked by its role default on a `NOLOGIN` role has no row in `pg_user`, so the
superuser flag read from that view answered NULL and access blocked as unreported, while
`pg_roles.rolsuper` answered false for a select-only role and true for a `NOLOGIN SUPERUSER`
role whose `INSERT` then succeeded (PostgreSQL 16.15, live). The identity question
therefore reads `pg_roles`, and the engine double here answers each catalog as the server did.
"""

from pathlib import Path
from typing import Any

import pytest

from auto_bi.verification.environment import EnvironmentFacts, EnvironmentQuestions, check_access
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import (
    Case,
    detail_of,
    probing,
    semantic_model,
    status_of,
    summary_of,
)
from tests.test_environment_probe import WRITE_STATEMENT, FakeEngine, RefusingEngine
from tests.test_postgres_column_write_privileges import pg_questions, role_engine
from tests.test_postgres_set_role_reach import (
    CLEAN,
    MART,
    cost_recorder,
    reach_sql,
    reachable,
    reachable_on,
    role_row,
    with_runtime,
)

IDENTITY = (
    "SELECT current_user AS name, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "AS superuser, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypassrls"
)


class NologinEngine(FakeEngine):
    """Answers as the live server did for a current role that cannot log in.

    `pg_user` lists only the roles that can log in, so a superuser flag read from it is
    NULL for such a role, while `pg_roles` reports the role's own flag (PostgreSQL 16.15).
    Every other question is answered as `FakeEngine` answers it.
    """

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        rows = super().__call__(sql)
        if "current_user AS name" in sql and "FROM pg_user" in sql:
            return [{**row, "superuser": None} for row in rows]
        return rows


def engine_for(name: str, superuser: bool | None, bypassrls: bool) -> NologinEngine:
    """A session parked on a role that cannot log in, with that role's catalog flags."""
    engine = NologinEngine(role_engine().answers)
    engine.answers["current_user AS name"] = [
        {"name": name, "superuser": superuser, "bypassrls": bypassrls}
    ]
    return engine


def collect_with_runtime(
    engine: FakeEngine | RefusingEngine, engine_name: str
) -> tuple[EnvironmentQuestions, EnvironmentFacts, list[str], list[str]]:
    scope = pg_questions()
    scope.engines = [engine_name]
    runtime_sql = with_runtime(scope)
    run_cost, calls = cost_recorder(runtime_sql)
    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    return scope, facts, runtime_sql, calls


def gate_for(tmp_path: Path, engine_name: str, facts: EnvironmentFacts) -> Any:
    model = semantic_model()
    for table in model.tables:
        table.physical.engine = engine_name
    return Case(tmp_path).with_candidate(model).run(probe=probing(facts))


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_select_only_nologin_current_role_stays_read_only(tmp_path: Path, engine_name: str) -> None:
    engine = engine_for("reader_only", False, False)
    reachable(engine, [{"name": "sess_a", "superuser": False}])
    reachable_on(engine, MART, ["sess_a"], [role_row("sess_a")])
    scope, facts, runtime_sql, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "reader_only"
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is False
    assert facts.role.evidence == CLEAN + ", for this role or any role SET ROLE reaches (sess_a)"
    assert check_access(scope, facts) == []
    assert calls == runtime_sql
    assert len(calls) == 1
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.PASS


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_select_only_nologin_current_role_with_empty_reach(engine_name: str) -> None:
    engine = engine_for("reader_only", False, False)
    reachable(engine, [])
    scope, facts, runtime_sql, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "reader_only"
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is False
    assert facts.role.evidence == CLEAN + "; SET ROLE reaches no other role"
    assert check_access(scope, facts) == []
    assert calls == runtime_sql


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_nologin_superuser_blocks_without_privileges_or_runtime(
    tmp_path: Path, engine_name: str
) -> None:
    engine = engine_for("su_nologin", True, False)
    _, facts, _, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "su_nologin"
    assert facts.role.read_only is False
    assert facts.role.evidence == "a superuser, whose writes no privilege check can stop"
    assert facts.role.bypasses_row_security is True
    assert not any("has_table_privilege" in sql for sql in engine.asked)
    assert reach_sql() not in engine.asked
    assert calls == []
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_nologin_bypassrls_role_reports_bypass(engine_name: str) -> None:
    engine = engine_for("bypass_nologin", False, True)
    reachable(engine, [{"name": "sess_a", "superuser": False}])
    reachable_on(engine, MART, ["sess_a"], [role_row("sess_a")])
    _, facts, runtime_sql, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "bypass_nologin"
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is True
    assert facts.role.evidence == CLEAN + ", for this role or any role SET ROLE reaches (sess_a)"
    assert calls == runtime_sql


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_writable_session_user_behind_nologin_role_blocks(tmp_path: Path, engine_name: str) -> None:
    engine = engine_for("reader_only", False, False)
    reachable(engine, [{"name": "sess_w", "superuser": False}])
    reachable_on(engine, MART, ["sess_w"], [role_row("sess_w", insert=True)])
    _, facts, _, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "reader_only"
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is False
    assert facts.role.evidence == "granted INSERT on dm.sales_daily through SET ROLE sess_w"
    assert calls == []
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_identity_question_reads_pg_roles_once(engine_name: str) -> None:
    engine = engine_for("reader_only", False, False)
    collect_with_runtime(engine, engine_name)

    identities = [sql for sql in engine.asked if "current_user AS name" in sql]
    assert identities == [IDENTITY]
    assert not any("pg_user" in sql for sql in engine.asked)


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_login_reader_keeps_read_only_facts(tmp_path: Path, engine_name: str) -> None:
    engine = role_engine()
    scope, facts, runtime_sql, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "gate_ro"
    assert facts.role.read_only is True
    assert facts.role.bypasses_row_security is False
    assert facts.role.evidence == CLEAN + "; SET ROLE reaches no other role"
    assert check_access(scope, facts) == []
    assert calls == runtime_sql
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.PASS


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_login_superuser_keeps_superuser_evidence(engine_name: str) -> None:
    engine = role_engine()
    engine.answers["current_user AS name"] = [{"name": "postgres", "superuser": True}]
    _, facts, _, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "postgres"
    assert facts.role.read_only is False
    assert facts.role.evidence == "a superuser, whose writes no privilege check can stop"
    assert facts.role.bypasses_row_security is True
    assert not any("has_table_privilege" in sql for sql in engine.asked)
    assert reach_sql() not in engine.asked
    assert calls == []


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_refused_identity_still_fails_closed(tmp_path: Path, engine_name: str) -> None:
    engine = RefusingEngine(role_engine(), "current_user AS name", sqlstate="42501")
    _, facts, _, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "unknown"
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is None
    assert facts.role.evidence == (
        "unreported role identity, superuser status and BYPASSRLS, "
        "so read-only access is not established"
    )
    assert not any("has_table_privilege" in sql for sql in engine.asked)
    assert reach_sql() not in engine.asked
    assert calls == []
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"
    assert "whose read-only access is not established" in detail_of(report, "dwh.access")


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_really_null_superuser_still_fails_closed(tmp_path: Path, engine_name: str) -> None:
    engine = engine_for("reader_only", None, False)
    _, facts, _, calls = collect_with_runtime(engine, engine_name)

    assert facts.role.name == "reader_only"
    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "unreported superuser status in role identity, so read-only access is not established"
    )
    assert facts.role.bypasses_row_security is None
    assert not any("has_table_privilege" in sql for sql in engine.asked)
    assert reach_sql() not in engine.asked
    assert calls == []
    report = gate_for(tmp_path, engine_name, facts)
    assert status_of(report, "dwh.access") is CheckStatus.BLOCK
    assert summary_of(report, "dwh.access") == "access.role_writable"
    assert "whose read-only access is not established" in detail_of(report, "dwh.access")


@pytest.mark.parametrize("engine_name", ["greenplum", "greengage", "postgres", "postgresql"])
def test_nologin_collection_never_sends_write_statement(engine_name: str) -> None:
    engine = engine_for("reader_only", False, False)
    reachable(engine, [{"name": "sess_w", "superuser": False}])
    reachable_on(engine, MART, ["sess_w"], [role_row("sess_w", insert=True)])
    collect_with_runtime(engine, engine_name)

    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
