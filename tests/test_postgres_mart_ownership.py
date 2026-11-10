"""Mart ownership affects access and the rows to which PostgreSQL policies apply."""

from typing import Any

import pytest

from auto_bi.verification.environment import (
    RowPolicyFacts,
    TableQuestion,
    check_access,
    check_security,
    denies_all_rows,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.model import Severity
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION
from tests.test_environment_probe import WRITE_STATEMENT, postgres_engine
from tests.test_postgres_column_write_privileges import pg_questions
from tests.test_postgres_kernel_row_security import IDENTITY, KernelError
from tests.test_postgres_set_role_reach import cost_recorder, with_runtime

MART = "dm.sales_daily"


class OwnershipEngine:
    """Catalog answers with DML revoked; ownership is independent of those grants."""

    def __init__(
        self,
        *,
        current: str = "reader",
        owner: str = "mart_owner",
        owns: bool = False,
        schema_owns: bool = False,
        reached: str | None = None,
        reached_owns: bool = False,
        reached_schema_owns: bool = False,
        force: bool = False,
        old: bool = False,
        superuser: bool = False,
        bypassrls: bool = False,
    ) -> None:
        self.inner = postgres_engine()
        self.asked: list[str] = []
        self.current = current
        self.owner = owner
        self.owns = owns
        self.schema_owns = schema_owns
        self.reached = reached
        self.reached_owns = reached_owns
        self.reached_schema_owns = reached_schema_owns
        self.force = force
        self.old = old
        self.superuser = superuser
        self.bypassrls = bypassrls
        self.omit: str | None = None
        self.null: str | None = None
        self.empty_name: str | None = None
        self.refuse = False
        self.empty = False
        self.policies: list[dict[str, Any]] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.asked.append(sql)
        if self.old and "rolbypassrls" in sql:
            raise KernelError('column "rolbypassrls" does not exist', "42703")
        if "AS version_num" in sql:
            return [{"version_num": 90426}]
        if "current_user AS name" in sql:
            return [
                {"name": self.current, "superuser": self.superuser, "bypassrls": self.bypassrls}
            ]
        if "SELECT r.rolname AS name, r.rolsuper" in sql:
            return [] if self.reached is None else [{"name": self.reached, "superuser": False}]
        if "has_table_privilege" in sql:
            reached = "has_table_privilege(r.oid," in sql
            incomplete = reached if self.reached is not None else not reached
            if incomplete and self.refuse:
                raise KernelError("permission denied", "42501")
            if incomplete and self.empty:
                return []
            row = dict.fromkeys(("insert", "update", "delete", "truncate"), False)
            row.update(
                name=self.reached,
                column_insert=False,
                column_update=False,
                owner_name=self.owner,
                owns_mart=self.reached_owns if reached else self.owns,
                schema_owner_name="schema_owner",
                owns_schema=self.reached_schema_owns if reached else self.schema_owns,
            )
            if incomplete and self.omit:
                row.pop(self.omit)
            if incomplete and self.null:
                row[self.null] = None
            if incomplete and self.empty_name:
                row[self.empty_name] = ""
            return [row]
        if "relrowsecurity" in sql:
            assert not self.old
            return [{"enabled": True, "owner_bypasses": self.owns and not self.force}]
        if "pg_policy" in sql:
            assert not self.old
            return self.policies
        return self.inner(sql)


def observed(engine: OwnershipEngine):
    scope = pg_questions()
    facts = collect(engine, scope, engine="postgres", target="ownership-test")
    assert not any(WRITE_STATEMENT.match(sql) for sql in engine.asked)
    return scope, facts


@pytest.mark.parametrize(
    ("options", "holder"),
    [
        ({"current": "mart_owner", "owns": True}, "current role mart_owner"),
        ({"owns": True}, "current role reader"),
        ({"reached": "mart_owner", "reached_owns": True}, "SET ROLE mart_owner"),
        (
            {"owner": "login_owner", "reached": "login_owner", "reached_owns": True},
            "SET ROLE login_owner",
        ),
        ({"reached": "member", "reached_owns": True}, "SET ROLE member"),
    ],
    ids=["owner-revoked-dml", "inherited-owner", "noinherit", "role-default", "reached-inherits"],
)
def test_mart_owner_blocks(options: dict[str, Any], holder: str) -> None:
    engine = OwnershipEngine(**options)
    scope, facts = observed(engine)
    assert facts.role.read_only is False
    assert "ownership" in facts.role.evidence
    assert MART in facts.role.evidence
    assert engine.owner in facts.role.evidence
    assert holder in facts.role.evidence
    assert "USAGE" in facts.role.evidence
    assert "access.role_writable" in {f.code for f in check_access(scope, facts)}
    writes = [sql for sql in engine.asked if "has_table_privilege" in sql]
    for sql in writes:
        who = "r.oid" if "has_table_privilege(r.oid," in sql else "current_user"
        assert f"pg_has_role({who}, c.relowner, 'USAGE')" in sql


@pytest.mark.parametrize("path", ["direct", "inherited", "set-role", "role-default"])
def test_schema_owner_blocks(path: str) -> None:
    reached = "schema_owner" if path in {"set-role", "role-default"} else None
    engine = OwnershipEngine(
        current="schema_owner" if path == "direct" else "reader",
        schema_owns=reached is None,
        reached=reached,
        reached_schema_owns=reached is not None,
    )
    scope, facts = observed(engine)
    assert facts.role.read_only is False
    assert "schema ownership" in facts.role.evidence
    assert "schema_owner" in facts.role.evidence and MART in facts.role.evidence
    assert "access.role_writable" in {f.code for f in check_access(scope, facts)}
    for sql in engine.asked:
        if "has_table_privilege" in sql:
            who = "r.oid" if "has_table_privilege(r.oid," in sql else "current_user"
            assert f"pg_has_role({who}, n.nspowner, 'USAGE')" in sql


@pytest.mark.parametrize(
    ("field", "answer"),
    [
        (field, answer)
        for field in ("owns_mart", "owns_schema", "owner_name", "schema_owner_name")
        for answer in ("omit", "null")
    ]
    + [(field, "empty_name") for field in ("owner_name", "schema_owner_name")],
)
@pytest.mark.parametrize("reached", [None, "member"])
def test_unreported_ownership_blocks(field: str, answer: str, reached: str | None) -> None:
    engine = OwnershipEngine(reached=reached)
    setattr(engine, answer, field)
    _, facts = observed(engine)
    assert facts.role.read_only is False
    assert "unreported" in facts.role.evidence and "ownership" in facts.role.evidence
    assert "read-only access is not established" in facts.role.evidence


@pytest.mark.parametrize("reached", [None, "member"])
@pytest.mark.parametrize("answer", ["refuse", "empty"])
def test_unanswered_ownership_blocks(reached: str | None, answer: str) -> None:
    engine = OwnershipEngine(reached=reached)
    setattr(engine, answer, True)
    _, facts = observed(engine)
    assert facts.role.read_only is False
    earlier = (
        f"write privileges of {reached} on {MART}"
        if reached
        else f"table INSERT/UPDATE/DELETE/TRUNCATE privileges on {MART}"
    )
    holder = f"SET ROLE {reached}" if reached else "current role reader"
    assert facts.role.evidence == (
        f"unreported {earlier}; ownership of {MART} for {holder}; "
        f"schema ownership of {MART} for {holder}, so read-only access is not established"
    )


@pytest.mark.parametrize("granted", [False, True])
def test_privilege_evidence_precedes_ownership_across_marts_and_roles(granted: bool) -> None:
    engine = OwnershipEngine(owns=True, schema_owns=True, reached="member", reached_owns=True)
    scope = pg_questions()
    marts = [MART, "dm.other"]
    scope.tables.append(TableQuestion(table=marts[1], event_time_column="date"))

    def query(sql: str):
        rows = engine(sql)
        if "has_table_privilege" in sql:
            rows[0].update(insert=granted or None, column_update=granted or None)
            if not granted:
                rows[0].update(owns_mart=None, owns_schema=None)
        elif " AS column_insert" in sql:
            rows[0].update(column_update=granted or None)
        return rows

    facts = collect(query, scope, engine="postgres", target="ownership-test")
    if granted:
        earlier = [
            item for mart in marts for item in (f"INSERT on {mart}", f"UPDATE on columns of {mart}")
        ]
        earlier += [f"{item} through SET ROLE member" for item in earlier]
        ownership = [
            item
            for mart in marts
            for item in (
                f"ownership of {mart} by mart_owner, held through current role reader (USAGE)",
                f"schema ownership of {mart} by schema_owner, "
                "held through current role reader (USAGE)",
            )
        ]
        ownership += [
            f"ownership of {mart} by mart_owner, held through SET ROLE member (USAGE)"
            for mart in marts
        ]
        expected = "granted " + "; ".join(earlier + ownership)
    else:
        earlier = [
            f"{kind} {privilege} privileges{role} on {mart}"
            for role in ("", " of member")
            for mart in marts
            for kind, privilege in (("table", "INSERT"), ("column", "UPDATE"))
        ]
        ownership = [
            f"{kind} of {mart} for {holder}"
            for holder in ("current role reader", "SET ROLE member")
            for mart in marts
            for kind in ("ownership", "schema ownership")
        ]
        expected = (
            "unreported "
            + "; ".join(earlier + ownership)
            + ", so read-only access is not established"
        )
    assert facts.role.evidence == expected


def test_select_only_nonowner_stays_read_only() -> None:
    engine = OwnershipEngine()
    _, facts = observed(engine)
    assert facts.role.read_only is True
    assert facts.tables[0].readable is True


@pytest.mark.parametrize("owns", [False, True])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("policy", ["none", "filter", "other-role", "restrictive"])
def test_owner_row_security(owns: bool, force: bool, policy: str) -> None:
    engine = OwnershipEngine(owns=owns, force=force)
    if policy != "none":
        engine.policies = [
            {
                "name": "subset",
                "permissive": policy != "restrictive",
                "applies": policy in {"filter", "restrictive"},
                "expression": "revenue > 0",
            }
        ]
    scope, facts = observed(engine)
    table = facts.tables[0]
    bypass = owns and not force
    assert facts.role.bypasses_row_security is False
    assert denies_all_rows(table, facts.role) is (not bypass and policy != "filter")
    codes = {f.code for f in check_access(scope, facts) + check_security(scope, facts)}
    if bypass:
        assert table.applying_policies() == []
        assert not codes & {
            "security.row_filtered",
            "security.row_policy_denies_all",
            "access.row_policy_denies_all",
        }
        assert ("security.row_security_bypassed" in codes) is (policy != "none")
    else:
        assert (
            "security.row_filtered" if policy == "filter" else "security.row_policy_denies_all"
        ) in codes
    assert table.owner_bypasses_row_security is bypass
    sql = next(sql for sql in engine.asked if "relrowsecurity" in sql)
    assert "pg_has_role(current_user, c.relowner, 'USAGE')" in sql
    assert "NOT c.relforcerowsecurity" in sql


def test_schema_or_reached_owner_does_not_bypass_current_row_security() -> None:
    engine = OwnershipEngine(schema_owns=True, reached="mart_owner", reached_owns=True)
    scope, facts = observed(engine)
    assert facts.tables[0].owner_bypasses_row_security is False
    assert denies_all_rows(facts.tables[0], facts.role)
    assert "security.row_policy_denies_all" in {f.code for f in check_security(scope, facts)}


def policy_reading(session: str, enabled: bool | None, policy: RowPolicyFacts):
    scope, facts = observed(OwnershipEngine())
    facts.role = facts.role.model_copy(
        update={"bypasses_row_security": session in {"bypassrls", "both"}}
    )
    facts.tables[0] = facts.tables[0].model_copy(
        update={
            "row_security_enabled": enabled,
            "owner_bypasses_row_security": session in {"owner", "both"},
            "row_policies": [policy],
            "column_reads": {"revenue": True},
        }
    )
    return scope, facts


@pytest.mark.parametrize("session", ["reader", "owner", "bypassrls"])
@pytest.mark.parametrize("restrictive", [False, True])
@pytest.mark.parametrize("sensitive", [False, True])
def test_disabled_row_security_has_no_policy_findings(
    session: str, restrictive: bool, sensitive: bool
) -> None:
    scope, facts = policy_reading(
        session,
        False,
        RowPolicyFacts(
            name="positive",
            applies_to_session=True,
            restrictive=restrictive,
            expression="revenue > 0",
        ),
    )
    if sensitive:
        scope.tables[0].sensitive_columns = ["revenue"]
    table = facts.tables[0]
    assert table.applying_policies() == []
    assert denies_all_rows(table, facts.role) is False
    findings = check_security(scope, facts)
    assert [f.code for f in findings] == (
        ["security.unenforced_classification"] if sensitive else []
    )
    if sensitive:
        assert findings[0].severity is Severity.REVIEW


@pytest.mark.parametrize("session", ["owner", "bypassrls", "both"])
@pytest.mark.parametrize("policy_kind", ["no-session", "all-rows"])
@pytest.mark.parametrize("enabled", [True, None])
def test_bypass_detail_states_established_exemptions(
    session: str, policy_kind: str, enabled: bool | None
) -> None:
    scope, facts = policy_reading(
        session,
        enabled,
        RowPolicyFacts(
            name="reported", applies_to_session=policy_kind == "all-rows", expression="true"
        ),
    )
    findings = check_security(scope, facts)
    assert [f.code for f in findings] == ["security.row_security_bypassed"]
    assert findings[0].severity is Severity.REVIEW
    reasons = []
    if session in {"bypassrls", "both"}:
        reasons.append("the role-wide row-security exemption (SUPERUSER or BYPASSRLS)")
    if session in {"owner", "both"}:
        reasons.append("the mart owner's privileges with FORCE ROW LEVEL SECURITY off")
    assert findings[0].detail == (
        f"{MART} on ownership-test reports reported — true; current role reader holds "
        + " and ".join(reasons)
        + "; the reported policies do not filter what this session read"
    )


@pytest.mark.parametrize(
    ("superuser", "bypassrls", "force"),
    [
        (True, False, True),
        (True, False, False),
        (False, True, True),
        (True, True, True),
        (True, True, False),
    ],
    ids=[
        "superuser-force-on",
        "superuser-force-off",
        "bypassrls-force-on",
        "both-force-on",
        "both-force-off",
    ],
)
def test_collected_identity_bypass_detail(superuser: bool, bypassrls: bool, force: bool) -> None:
    engine = OwnershipEngine(owns=True, force=force, superuser=superuser, bypassrls=bypassrls)
    engine.policies = [
        {"name": "reported", "permissive": True, "applies": True, "expression": "true"}
    ]
    scope, facts = observed(engine)
    assert IDENTITY in engine.asked
    row_security_sql = next(sql for sql in engine.asked if "relrowsecurity" in sql)
    assert "NOT c.relforcerowsecurity" in row_security_sql
    assert facts.role.bypasses_row_security is True
    assert facts.tables[0].row_security_enabled is True
    assert facts.tables[0].owner_bypasses_row_security is (not force)
    findings = check_security(scope, facts)
    assert [f.code for f in findings] == ["security.row_security_bypassed"]
    assert findings[0].severity is Severity.REVIEW
    exemptions = "the role-wide row-security exemption (SUPERUSER or BYPASSRLS)"
    if not force:
        exemptions += " and the mart owner's privileges with FORCE ROW LEVEL SECURITY off"
    assert findings[0].detail == (
        f"{MART} on ownership-test reports reported — true; current role reader holds "
        f"{exemptions}; the reported policies do not filter what this session read"
    )
    assert "BYPASSRLS exemption" not in findings[0].detail


@pytest.mark.parametrize("session", ["reader", "owner", "bypassrls"])
@pytest.mark.parametrize("restrictive", [False, True])
def test_unknown_row_security_switch_keeps_findings(session: str, restrictive: bool) -> None:
    scope, facts = policy_reading(
        session,
        None,
        RowPolicyFacts(name="reported", applies_to_session=True, restrictive=restrictive),
    )
    table = facts.tables[0]
    assert table.applying_policies() == ([] if session == "owner" else table.row_policies)
    assert denies_all_rows(table, facts.role) is False
    assert [f.code for f in check_security(scope, facts)] == [
        "security.row_filtered" if session == "reader" else "security.row_security_bypassed"
    ]


@pytest.mark.parametrize("owns", [False, True])
def test_94_ownership_without_row_security_catalogs(owns: bool) -> None:
    engine = OwnershipEngine(owns=owns, old=True)
    _, facts = observed(engine)
    assert facts.role.read_only is not owns
    assert facts.server_version_num == 90426
    assert facts.tables[0].owner_bypasses_row_security is None
    assert facts.tables[0].row_policies == []
    assert not any("relforcerowsecurity" in sql or "pg_policy" in sql for sql in engine.asked)
    sql = next(sql for sql in engine.asked if "has_table_privilege" in sql)
    assert "c.relowner" in sql and "n.nspowner" in sql


@pytest.mark.parametrize("schema", [False, True])
def test_94_reached_ownership(schema: bool) -> None:
    engine = OwnershipEngine(
        old=True, reached="holder", reached_owns=not schema, reached_schema_owns=schema
    )
    _, facts = observed(engine)
    assert facts.role.read_only is False
    assert "ownership" in facts.role.evidence
    assert "SET ROLE holder" in facts.role.evidence
    assert not any("relforcerowsecurity" in sql for sql in engine.asked)


@pytest.mark.parametrize("answer", [None, []])
def test_unreported_owner_bypass_stays_unknown(answer: Any) -> None:
    engine = OwnershipEngine()

    def ask(sql: str):
        if "relrowsecurity" in sql:
            return [] if answer == [] else [{"enabled": True, "owner_bypasses": answer}]
        return engine(sql)

    facts = collect(ask, pg_questions(), engine="postgres", target="ownership-test")
    assert facts.tables[0].owner_bypasses_row_security is None


def test_view_ownership_uses_pg_class_without_table_kind_filter() -> None:
    engine = OwnershipEngine(owns=True)
    engine.inner.answers["pg_tables"] = [{"tablename": "sales_daily"}]

    def ask(sql: str) -> list[dict[str, Any]]:
        rows = engine(sql)
        if "relrowsecurity" in sql:
            rows[0]["enabled"] = False
        return rows

    scope = pg_questions()
    facts = collect(ask, scope, engine="postgres", target="ownership-test")
    assert facts.role.read_only is False
    assert facts.tables[0].owner_bypasses_row_security is True
    assert facts.tables[0].row_security_enabled is False
    assert facts.tables[0].row_policies == []
    assert check_security(scope, facts) == []
    sql = next(sql for sql in engine.asked if "has_table_privilege" in sql)
    assert "pg_class" in sql and "relkind" not in sql
    assert all(
        catalog not in sql
        for sql in engine.asked
        for catalog in ("pg_inherits", "pg_depend", "pg_rewrite")
    )
    for sql in engine.asked:
        if "has_table_privilege" in sql or "relrowsecurity" in sql or "pg_policy" in sql:
            assert 'to_regclass(\'"dm"."sales_daily"\')' in sql


def test_absent_mart_keeps_absence_fact() -> None:
    engine = OwnershipEngine()
    engine.inner.answers["pg_tables"] = []
    _, facts = observed(engine)
    assert facts.tables[0].exists is False
    assert facts.tables[0].readable is False


def test_owner_bypass_is_per_mart() -> None:
    engine = OwnershipEngine(owns=True)
    scope = pg_questions()
    scope.tables.append(TableQuestion(table="dm.other", event_time_column="date"))

    def ask(sql: str) -> list[dict[str, Any]]:
        return OwnershipEngine()(sql) if '"dm"."other"' in sql else engine(sql)

    facts = collect(ask, scope, engine="postgres", target="ownership-test")
    assert facts.tables[0].owner_bypasses_row_security is True
    assert facts.tables[1].owner_bypasses_row_security is False
    findings = check_security(scope, facts)
    assert [(f.code, f.subject) for f in findings] == [
        ("security.row_policy_denies_all", "dm.other")
    ]


@pytest.mark.parametrize("owns", [False, True])
def test_ownership_withholds_exact_runtime(owns: bool) -> None:
    engine = OwnershipEngine(owns=owns)
    engine.policies = [{"name": "all", "permissive": True, "applies": True, "expression": "true"}]
    scope = pg_questions()
    sql = with_runtime(scope)
    run_cost, calls = cost_recorder(sql)
    collect(engine, scope, engine="postgres", target="ownership-test", run_cost=run_cost)
    assert calls == ([] if owns else sql)


def test_ownership_versions() -> None:
    assert int(VERIFIER_VERSION) >= 123
    assert int(CHECK_VERSION) >= 81
