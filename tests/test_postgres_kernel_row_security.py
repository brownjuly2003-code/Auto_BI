"""T-41: grade the catalogs available on the live PostgreSQL 9.4.26 kernel."""

from pathlib import Path
from typing import Any

import pytest

from auto_bi.verification.environment import check_access, check_security
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CheckStatus
from tests.test_decision_verify import Case, _check_payload, environment
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
    pg_questions,
    role_engine,
    table_sql,
)
from tests.test_postgres_set_role_reach import (
    CLEAN,
    MART,
    cost_recorder,
    reachable,
    reachable_on,
    role_row,
    with_runtime,
)

IDENTITY = (
    "SELECT current_user AS name, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "AS superuser, (SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypassrls"
)
VERSION = "SELECT current_setting('server_version_num')::int AS version_num"
OLD_IDENTITY = (
    "SELECT current_user AS name, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) "
    "AS superuser"
)
TABLE_WRITE = (
    "SELECT has_table_privilege('dm.sales_daily', 'INSERT') AS insert, "
    "has_table_privilege('dm.sales_daily', 'UPDATE') AS update, "
    "has_table_privilege('dm.sales_daily', 'DELETE') AS delete, "
    "has_table_privilege('dm.sales_daily', 'TRUNCATE') AS truncate, "
    "pg_get_userbyid(c.relowner) AS owner_name, "
    "pg_has_role(current_user, c.relowner, 'USAGE') AS owns_mart, "
    "pg_get_userbyid(n.nspowner) AS schema_owner_name, "
    "pg_has_role(current_user, n.nspowner, 'USAGE') AS owns_schema "
    "FROM (SELECT to_regclass('dm.sales_daily') AS oid) mart "
    "LEFT JOIN pg_class c ON c.oid = mart.oid "
    "LEFT JOIN pg_namespace n ON n.oid = c.relnamespace"
)
COLUMN_WRITE = (
    "SELECT has_any_column_privilege('dm.sales_daily', 'INSERT') AS column_insert, "
    "has_any_column_privilege('dm.sales_daily', 'UPDATE') AS column_update"
)
REACH = (
    "SELECT r.rolname AS name, r.rolsuper AS superuser FROM pg_roles r "
    "WHERE (r.rolsuper OR NOT pg_has_role(current_user, r.oid, 'USAGE')) AND "
    "(r.rolname = session_user OR pg_has_role(session_user, r.oid, "
    "CASE WHEN current_setting('server_version_num')::int >= 160000 "
    "THEN 'SET' ELSE 'MEMBER' END)) ORDER BY r.rolname"
)
MART_QUESTIONS = [
    "SELECT tablename FROM pg_tables WHERE schemaname = 'dm' AND tablename = 'sales_daily' "
    "UNION ALL SELECT viewname FROM pg_views WHERE schemaname = 'dm' AND viewname = 'sales_daily'",
    "SELECT a.attname AS name, has_column_privilege(a.attrelid, a.attnum, 'SELECT') AS ok "
    "FROM pg_attribute a WHERE a.attrelid = to_regclass('dm.sales_daily') "
    "AND a.attnum > 0 AND NOT a.attisdropped AND a.attname IN ('date', 'revenue')",
    "SELECT has_any_column_privilege('dm.sales_daily', 'SELECT') AS ok",
    'SELECT count(*) AS rows_seen, max("date") AS max_event_time FROM "dm"."sales_daily"',
    "SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type "
    "FROM pg_attribute a WHERE a.attrelid = to_regclass('dm.sales_daily') "
    "AND a.attnum > 0 AND NOT a.attisdropped",
]
PLAN = "EXPLAIN (FORMAT JSON) SELECT sum(revenue) FROM dm.sales_daily"


class KernelError(Exception):
    def __init__(self, text: str, state: str) -> None:
        super().__init__(text)
        self.sqlstate = state


class KernelEngine:
    """Missing RLS catalogs answer as on 9.4; all other answers use existing fixtures."""

    def __init__(self) -> None:
        self.inner: FakeEngine | RefusingEngine = role_engine()
        self.inner.answers["has_column_privilege(a.attrelid"] = [
            {"name": name, "ok": True} for name in ("date", "revenue")
        ]
        self.asked: list[str] = []
        self.version: Any = [{"version_num": 90426}]
        self.identity: Any = [{"name": "gate_ro", "superuser": False}]
        self.error: Exception = KernelError('column "rolbypassrls" does not exist', "42703")

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.asked.append(sql)
        if "rolbypassrls" in sql or "relrowsecurity" in sql:
            raise self.error
        if "pg_policy" in sql:
            raise KernelError('relation "pg_policy" does not exist', "42P01")
        answer = self.version if sql == VERSION else self.identity if sql == OLD_IDENTITY else None
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        return self.inner(sql)


@pytest.fixture(params=["greenplum", "greengage", "postgres", "postgresql"])
def engine_name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def observed(engine: Any, engine_name: str) -> Any:
    scope = pg_questions()
    scope.engines = [engine_name]
    runtime = with_runtime(scope)
    run_cost, calls = cost_recorder(runtime)
    facts = collect(engine, scope, engine=engine_name, target="stand", run_cost=run_cost)
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    return scope, facts, runtime, calls


def test_select_only_role_is_read_only(engine_name: str) -> None:
    scope, facts, runtime, calls = observed(KernelEngine(), engine_name)
    assert facts.server_version_num == 90426
    assert facts.predates_row_security() is True
    assert facts.role.name == "gate_ro"
    assert facts.role.read_only is True
    assert facts.role.evidence == CLEAN + "; SET ROLE reaches no other role"
    assert facts.role.bypasses_row_security is None
    assert facts.tables[0].row_policies == []
    assert facts.tables[0].row_security_enabled is None
    assert check_access(scope, facts) == check_security(scope, facts) == []
    assert calls == runtime


def test_questions_of_old_kernel(engine_name: str) -> None:
    engine = KernelEngine()
    observed(engine, engine_name)
    assert engine.asked == [
        IDENTITY,
        VERSION,
        OLD_IDENTITY,
        TABLE_WRITE,
        COLUMN_WRITE,
        REACH,
        *MART_QUESTIONS,
        PLAN,
    ]


def test_clean_security_detail_names_kernel(tmp_path: Path, engine_name: str) -> None:
    _, collected, _, _ = observed(KernelEngine(), engine_name)
    facts = environment()
    facts.server_version_num = collected.server_version_num
    report = Case(tmp_path).run(probe=lambda scope: facts)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.unfiltered"
    assert check.detail == (
        "security observation on 127.0.0.1:8124/dm: 4 of 4 requested column reads confirmed "
        "and 1 of 1 row-policy questions answered per in-scope mart/event-time question; "
        "coverage is complete; 127.0.0.1:8124/dm reports server_version_num 90426, "
        "a kernel without row-level security, so no row-policy catalog was read; "
        "no restrictions found in the observed facts for gate_ro"
    )
    payload = _check_payload(tmp_path, report, "dwh.security")
    assert payload["facts"]["server_version_num"] == 90426


@pytest.mark.parametrize("grant", ["table", "column", "reachable"])
def test_writable_role_blocks(engine_name: str, grant: str) -> None:
    engine = KernelEngine()
    if grant == "table":
        engine.inner.answers[table_sql(MART)][0]["insert"] = True
        evidence = "granted INSERT on dm.sales_daily"
    elif grant == "column":
        engine.inner.answers[column_sql(MART)][0]["column_update"] = True
        evidence = "granted UPDATE on columns of dm.sales_daily"
    else:
        reachable(engine.inner, [{"name": "writer", "superuser": False}])
        reachable_on(engine.inner, MART, ["writer"], [role_row("writer", insert=True)])
        evidence = "granted INSERT on dm.sales_daily through SET ROLE writer"
    scope, facts, _, calls = observed(engine, engine_name)
    assert facts.role.evidence == evidence
    assert facts.role.read_only is False
    assert facts.role.bypasses_row_security is None
    assert [item.code for item in check_access(scope, facts)] == ["access.role_writable"]
    assert check_security(scope, facts) == []
    assert calls == []


def test_superuser_keeps_evidence(engine_name: str) -> None:
    engine = KernelEngine()
    engine.identity = [{"name": "gate_ro", "superuser": True}]
    scope, facts, _, calls = observed(engine, engine_name)
    assert facts.role.evidence == "a superuser, whose writes no privilege check can stop"
    assert facts.role.bypasses_row_security is None
    assert [item.code for item in check_access(scope, facts)] == ["access.role_writable"]
    assert engine.asked == [IDENTITY, VERSION, OLD_IDENTITY, *MART_QUESTIONS, PLAN]
    assert calls == []


@pytest.mark.parametrize(
    "answer,fields",
    [
        (KernelError("permission denied", "42501"), None),
        ([], None),
        ([{"name": None, "superuser": False}], "role name"),
        ([{"name": "", "superuser": False}], "role name"),
        ([{"name": "gate_ro", "superuser": None}], "superuser status"),
        ([{}], "role name, superuser status"),
    ],
)
def test_unanswered_identity_blocks(engine_name: str, answer: Any, fields: str | None) -> None:
    engine = KernelEngine()
    engine.identity = answer
    scope, facts, _, calls = observed(engine, engine_name)
    expected = (
        "unreported role identity and superuser status"
        if fields is None
        else f"unreported {fields} in role identity"
    ) + ", so read-only access is not established"
    assert facts.role.evidence == expected
    assert facts.role.name == ("gate_ro" if fields == "superuser status" else "unknown")
    assert facts.role.bypasses_row_security is None
    assert [item.code for item in check_access(scope, facts)] == ["access.role_writable"]
    assert engine.asked == [IDENTITY, VERSION, OLD_IDENTITY, *MART_QUESTIONS, PLAN]
    assert calls == []


@pytest.mark.parametrize(
    "answer",
    [
        [{"version_num": value}]
        for value in (90500, 160015, -1, None, True, "90426", "9.4.26", 90426.0)
    ]
    + [[], [{}], KernelError("permission denied", "42501")],
)
def test_invalid_version_reraises_original(engine_name: str, answer: Any) -> None:
    engine = KernelEngine()
    engine.version = answer
    with pytest.raises(Exception) as caught:
        observed(engine, engine_name)
    assert caught.value is engine.error
    assert engine.asked == [IDENTITY, VERSION]


@pytest.mark.parametrize("answer", ["refused", "empty", "lost"])
def test_other_identity_error_does_not_ask_version(engine_name: str, answer: str) -> None:
    if answer == "lost":
        old = KernelEngine()
        old.error = ConnectionError("lost connection")
        with pytest.raises(ConnectionError):
            observed(old, engine_name)
        assert old.asked == [IDENTITY]
        return
    inner = postgres_engine(**{"current_user AS name": []})
    engine = RefusingEngine(inner, IDENTITY, sqlstate="42501") if answer == "refused" else inner
    facts = collect(engine, pg_questions(), engine=engine_name, target="stand")
    assert facts.role.read_only is False
    assert facts.role.evidence == (
        "unreported role identity, superuser status and BYPASSRLS, "
        "so read-only access is not established"
    )
    assert VERSION not in inner.asked
    assert facts.server_version_num is None


@pytest.mark.parametrize(
    "text",
    [
        'column "rolbypassrls" does not exist',
        "COLUMN rolbypassrls DOES NOT EXIST",
        'column "rolsuper" does not exist',
    ],
)
def test_driver_without_state_recognised_by_wording(engine_name: str, text: str) -> None:
    engine = KernelEngine()
    engine.error = Exception(text)
    if "rolsuper" in text:
        with pytest.raises(Exception) as caught:
            observed(engine, engine_name)
        assert caught.value is engine.error
        assert engine.asked == [IDENTITY]
    else:
        scope, facts, _, calls = observed(engine, engine_name)
        assert facts.server_version_num == 90426
        assert check_access(scope, facts) == check_security(scope, facts) == []
        assert calls


def test_modern_kernel_questions_unchanged(engine_name: str) -> None:
    engine = role_engine()
    scope, facts, _, calls = observed(engine, engine_name)
    assert facts.server_version_num is None
    assert facts.role.bypasses_row_security is False
    assert VERSION not in engine.asked
    assert engine.asked[0] == IDENTITY
    assert engine.asked[1:4] == [TABLE_WRITE, COLUMN_WRITE, REACH]
    assert check_access(scope, facts) == []
    assert calls


def test_restricted_mart_still_needs_review(engine_name: str) -> None:
    scope, facts, _, _ = observed(KernelEngine(), engine_name)
    scope.tables[0].sensitive_columns = ["revenue"]
    assert [item.code for item in check_security(scope, facts)] == [
        "security.unenforced_classification"
    ]
    assert check_access(scope, facts) == []


def test_clickhouse_unchanged() -> None:
    engine = clickhouse_engine()
    facts = collect(engine, questions(), engine="clickhouse", target="stand")
    assert facts.server_version_num is None
    assert facts.role.read_only is True
    assert IDENTITY not in engine.asked
    assert VERSION not in engine.asked


def test_version_runtime_fault_propagates(engine_name: str) -> None:
    engine = KernelEngine()
    error = ConnectionError("lost connection")
    engine.version = error
    with pytest.raises(ConnectionError) as caught:
        observed(engine, engine_name)
    assert caught.value is error
    assert engine.asked == [IDENTITY, VERSION]


@pytest.mark.parametrize("path", ["absent", "unreadable", "unanswered_reading", "present"])
def test_all_mart_return_paths(engine_name: str, path: str) -> None:
    engine = KernelEngine()
    if path == "absent":
        engine.inner.answers["pg_tables"] = []
    elif path == "unreadable":
        engine.inner.answers[
            "SELECT has_any_column_privilege('dm.sales_daily', 'SELECT') AS ok"
        ] = [{"ok": False}]
    elif path == "unanswered_reading":
        engine.inner.answers["count(*)"] = []
    _, facts, _, calls = observed(engine, engine_name)
    mart = facts.tables[0]
    assert mart.exists is (path != "absent")
    assert mart.readable is (path == "present")
    assert mart.row_security_enabled is None
    assert mart.row_policies == (None if path == "absent" else [])
    assert bool(calls) is (path == "present")
    assert not any("pg_policy" in sql or "relrowsecurity" in sql for sql in engine.asked)


def test_pgcode_is_recognised(engine_name: str) -> None:
    engine = KernelEngine()
    engine.error = Exception("driver wording differs")
    engine.error.pgcode = "42703"
    _, facts, _, _ = observed(engine, engine_name)
    assert facts.server_version_num == 90426


def test_other_state_does_not_use_wording(engine_name: str) -> None:
    engine = KernelEngine()
    engine.error = KernelError('column "rolbypassrls" does not exist', "08006")
    with pytest.raises(KernelError) as caught:
        observed(engine, engine_name)
    assert caught.value is engine.error
    assert engine.asked == [IDENTITY]


def test_only_first_version_row_counts(engine_name: str) -> None:
    engine = KernelEngine()
    engine.version = [{"version_num": 90500}, {"version_num": 90426}]
    with pytest.raises(KernelError) as caught:
        observed(engine, engine_name)
    assert caught.value is engine.error
    assert engine.asked == [IDENTITY, VERSION]


@pytest.mark.parametrize("exists", [False, True])
def test_no_readable_mart_keeps_detail_of_any_kernel(tmp_path: Path, exists: bool) -> None:
    facts = environment(exists=exists)
    facts.server_version_num = 90426
    facts.tables[0].readable = False
    report = Case(tmp_path).run(probe=lambda scope: facts)
    check = next(item for item in report.checks if item.check_id == "dwh.security")
    assert check.status is CheckStatus.PASS
    assert check.summary_code == "security.not_run"
    assert check.detail == (
        "no in-scope mart could be read on 127.0.0.1:8124/dm, so nothing was asked "
        "about what narrows it"
    )
    assert _check_payload(tmp_path, report, "dwh.security")["facts"]["server_version_num"] == 90426
