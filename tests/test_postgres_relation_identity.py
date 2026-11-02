"""Every mart question retains the schema and table's case."""

from __future__ import annotations

import re
from typing import Any

import pytest

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.semantic.gateway import SemanticSnapshot, _build_graph
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.verification import environment_probe as probe
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    TableQuestion,
    check_access,
    check_security,
    questions_for,
)
from auto_bi.verification.environment_probe import UnsafeIdentifierError, collect
from auto_bi.verification.model import Severity
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION
from tests.test_catalog_literals import Catalog, read_literals
from tests.test_decision_verify import metric_contract, product_contract, semantic_model
from tests.test_postgres_kernel_row_security import KernelError

MARTS = ["dm.Sales", "DM.sales"]
ENGINES = ["postgres", "greenplum", "greengage"]
SITES = [
    "has_table_privilege(",
    "AS column_insert",
    "has_table_privilege(r.oid,",
    "has_column_privilege(",
    "format_type(",
    "FROM pg_policy",
    "AS owner_bypasses",
    "'SELECT') AS ok",
]


class RelationCatalog:
    """Two distinct catalog entries, selected by the name each SQL function receives."""

    def __init__(self, mart: str, mode: str = "owner", *, old: bool = False) -> None:
        self.mart = mart
        self.mode = mode
        self.old = old
        self.asked: list[str] = []
        self.refused = ""

    def exact(self, sql: str, function: str) -> bool:
        match = re.search(rf"\b{function}\((?:r\.oid, )?'([^']+)'", sql)
        assert match is not None, sql
        parts = match[1].split(".")
        resolved = ".".join(p[1:-1] if p.startswith('"') else p.lower() for p in parts)
        assert resolved in {self.mart, "dm.sales"}, resolved
        return resolved == self.mart

    def active(self, sql: str) -> bool:
        return ("r.oid," in sql) == self.mode.startswith("reached_")

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.asked.append(sql)
        if self.refused and self.refused in sql:
            raise KernelError("permission denied", "42501")
        if "current_user AS name" in sql:
            if self.old and "rolbypassrls" in sql:
                raise KernelError('column "rolbypassrls" does not exist', "42703")
            return [{"name": "reader", "superuser": False, "bypassrls": False}]
        if "AS version_num" in sql:
            return [{"version_num": 90426}]
        if "SELECT r.rolname AS name, r.rolsuper" in sql:
            return [{"name": "holder", "superuser": False}]
        if "has_table_privilege(" in sql:
            exact_grants = self.exact(sql, "has_table_privilege")
            exact_owner = self.exact(sql, "to_regclass")
            row: dict[str, Any] = dict.fromkeys(("insert", "update", "delete", "truncate"), False)
            row.update(
                name="holder",
                insert=self.active(sql) and exact_grants and self.mode.endswith("grant"),
                owner_name="exact_owner" if exact_owner else "folded_owner",
                owns_mart=self.active(sql) and exact_owner and self.mode.endswith("owner"),
                schema_owner_name="exact_schema_owner" if exact_owner else "folded_schema_owner",
                owns_schema=self.active(sql) and exact_owner and self.mode.endswith("schema"),
            )
            if self.mode == "select_only":
                row.update(insert=not exact_grants, owns_mart=not exact_owner)
            if "has_any_column_privilege(" in sql:
                row.update(self.column_writes(sql))
            return [row]
        if "AS column_insert" in sql:
            return [self.column_writes(sql)]
        if "FROM pg_tables" in sql:
            schema, name = self.mart.split(".")
            assert f"schemaname = '{schema}'" in sql and f"tablename = '{name}'" in sql
            return [{"tablename": name}]
        if "has_column_privilege(" in sql:
            exact = self.exact(sql, "to_regclass")
            return [{"name": "date", "ok": True}, {"name": "amount", "ok": not exact}]
        if "format_type(" in sql:
            exact = self.exact(sql, "to_regclass")
            return [
                {"name": "date", "type": "date"},
                {"name": "amount", "type": "numeric" if exact else "text"},
            ]
        if "FROM pg_policy" in sql:
            assert not self.old
            exact = self.exact(sql, "to_regclass")
            return [
                {
                    "name": "exact_policy" if exact else "folded_policy",
                    "permissive": True,
                    "applies": exact,
                    "expression": "amount > 0" if exact else "true",
                }
            ]
        if "AS owner_bypasses" in sql:
            assert not self.old
            exact = self.exact(sql, "to_regclass")
            return [{"enabled": exact, "owner_bypasses": exact and self.mode == "owner"}]
        if "'SELECT') AS ok" in sql:
            exact = self.exact(sql, "has_any_column_privilege")
            return [{"ok": not (exact and self.mode == "unreadable")}]
        if "AS rows_seen" in sql:
            assert 'FROM "' + self.mart.replace(".", '"."') + '"' in sql
            return [{"rows_seen": 2, "max_event_time": None}]
        raise AssertionError(sql)

    def column_writes(self, sql: str) -> dict[str, bool]:
        return {
            "column_insert": False,
            "column_update": self.active(sql)
            and self.exact(sql, "has_any_column_privilege")
            and self.mode.endswith("column"),
        }


def observed(
    catalog: RelationCatalog, engine: str = "postgres"
) -> tuple[EnvironmentQuestions, EnvironmentFacts]:
    scope = EnvironmentQuestions(
        tables=[
            TableQuestion(
                table=catalog.mart,
                event_time_column="date",
                columns={"date": "date", "amount": "numeric"},
            )
        ]
    )
    return scope, collect(catalog, scope, engine=engine, target="relation-test")


@pytest.mark.parametrize("mart", MARTS)
@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("site", SITES)
def test_catalog_question_uses_quoted_relation(mart: str, engine: str, site: str) -> None:
    catalog = RelationCatalog(mart)
    observed(catalog, engine)
    asked = [sql for sql in catalog.asked if site in sql]
    assert asked
    for sql in asked:
        assert "'" + '"' + mart.replace(".", '"."') + '"' + "'" in sql
        assert f"'{mart}'" not in sql
        assert "::text" not in sql.replace("p.polcmd::text", "p.polcmd")


@pytest.mark.parametrize("mart", MARTS)
@pytest.mark.parametrize("kind", ["external_ref", "reviewed_sql"])
def test_valid_metric_keeps_mixed_case_grain(mart: str, kind: str) -> None:
    raw = metric_contract()
    raw["grain"]["table"] = mart
    raw["formula"] = (
        {"kind": kind, "external": {"source": "dbt", "ref": "sales", "source_revision": "abc123"}}
        if kind == "external_ref"
        else {
            "kind": kind,
            "sql": "SELECT 1",
            "review": {
                "reviewer": "reviewer",
                "reviewed_at": "2026-11-02T00:00:00Z",
                "rationale": "fixture",
            },
        }
    )
    metric = MetricContract.model_validate(raw)
    graph = _build_graph(semantic_model(), [metric], [])
    digest = sha256_digest(graph.model_dump(mode="json"))
    snapshot = SemanticSnapshot(
        source_kind="native_model_yaml",
        source_version="3",
        source_digest=digest,
        canonical_digest=digest,
        graph=graph,
    )
    scope = questions_for(DecisionProductSpec.model_validate(product_contract()), snapshot)
    assert [table.table for table in scope.tables] == [mart]


@pytest.mark.parametrize("mart", MARTS)
@pytest.mark.parametrize("old", [False, True])
@pytest.mark.parametrize(
    "mode",
    [
        "owner",
        "schema",
        "grant",
        "column",
        "reached_owner",
        "reached_schema",
        "reached_grant",
        "reached_column",
    ],
)
def test_exact_mart_write_capability_blocks(mart: str, old: bool, mode: str) -> None:
    catalog = RelationCatalog(mart, mode, old=old)
    scope, facts = observed(catalog)
    assert facts.role.read_only is False
    findings = check_access(scope, facts)
    assert any(f.code == "access.role_writable" and f.severity is Severity.BLOCK for f in findings)
    assert mart in facts.role.evidence
    if mode.startswith("reached_"):
        assert "SET ROLE holder" in facts.role.evidence
    if mode.endswith(("owner", "schema")):
        assert "ownership" in facts.role.evidence and "exact_" in facts.role.evidence


@pytest.mark.parametrize("mart", MARTS)
@pytest.mark.parametrize("old", [False, True])
def test_select_only_exact_mart_stays_read_only(mart: str, old: bool) -> None:
    scope, facts = observed(RelationCatalog(mart, "select_only", old=old))
    assert facts.role.read_only is True
    assert not any(f.code == "access.role_writable" for f in check_access(scope, facts))


@pytest.mark.parametrize("mart", MARTS)
def test_exact_column_types_reads_and_policies(mart: str) -> None:
    _, facts = observed(RelationCatalog(mart, "reader"))
    table = facts.tables[0]
    assert table.columns == {"date": "date", "amount": "numeric"}
    assert table.column_reads == {"date": True, "amount": False}
    assert table.row_security_enabled is True
    assert table.row_policies is not None
    assert [(p.name, p.expression, p.applies_to_session) for p in table.row_policies] == [
        ("exact_policy", "amount > 0", True)
    ]


@pytest.mark.parametrize("mart", MARTS)
def test_exact_owner_bypass(mart: str) -> None:
    scope, facts = observed(RelationCatalog(mart))
    assert facts.tables[0].owner_bypasses_row_security is True
    assert "security.row_security_bypassed" in {f.code for f in check_security(scope, facts)}


@pytest.mark.parametrize("mart", MARTS)
def test_exact_readability(mart: str) -> None:
    catalog = RelationCatalog(mart, "unreadable")
    _, facts = observed(catalog)
    assert facts.tables[0].readable is False
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


@pytest.mark.parametrize(
    "site,field",
    [
        ("has_column_privilege(", "column_reads"),
        ("FROM pg_policy", "row_policies"),
        ("AS owner_bypasses", "owner_bypasses_row_security"),
    ],
)
def test_unanswered_exact_fact_remains_unreported(site: str, field: str) -> None:
    catalog = RelationCatalog("dm.Sales")
    catalog.refused = site
    _, facts = observed(catalog)
    assert getattr(facts.tables[0], field) is None


def test_relation_identity_version() -> None:
    assert VERIFIER_VERSION == "127"
    assert CHECK_VERSION == "81"


PART_NAMES = [
    ("a.b.c", "b.c", '\'"a"."b.c"\''),
    ("dm..sales_daily", ".sales_daily", '\'"dm".".sales_daily"\''),
    ('dm.Sa"les', 'Sa"les', '\'"dm"."Sa""les"\''),
    ('d"m.sales', 'd"m', '\'"d""m"."sales"\''),
]


class PartCatalog(Catalog):
    """Current database a contains a."b.c" and b.c with distinct access and rows."""

    def __init__(self, engine: str, mart: str, *, old: bool = False) -> None:
        super().__init__(engine)
        self.current_database = "a"
        schema, _, name = mart.partition(".")
        self.exact_pair = (schema, name)
        self.old = old
        self.entries: dict[tuple[str, str], tuple[str, frozenset[str], int]] = {
            self.exact_pair: ("exact_owner", frozenset({"SELECT(date)"}), 2),
            ("b", "c"): ("gate", frozenset({"SELECT"}), 7),
        }
        self.catalog_pairs: list[tuple[str, str]] = []
        self.read_pairs: list[tuple[str, str]] = []

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        pair = self.exact_pair
        for literal in re.findall(
            r"(?:has_table_privilege|has_any_column_privilege|to_regclass)"
            r"\((?:r\.oid, )?('(?:''|[^'])*')",
            sql,
        ):
            _, values = read_literals(literal, self.engine)
            matched = re.fullmatch(r'"((?:[^"]|"")*)"\."((?:[^"]|"")*)"', values[0])
            assert matched is not None, values[0]
            pair = (matched[1].replace('""', '"'), matched[2].replace('""', '"'))
            assert pair == self.exact_pair, (sql, pair)
            self.catalog_pairs.append(pair)
        if "FROM pg_tables" in sql or "FROM system.tables" in sql:
            self.asked.append(sql)
            _, values = read_literals(sql, self.engine)
            assert values[:2] == list(self.exact_pair)
            return [{"tablename": pair[1], "engine": "MergeTree"}] if self.present else []
        if "AS rows_seen" in sql:
            self.asked.append(sql)
            parts = re.findall(r'"((?:[^"]|"")*)"', sql.split(" FROM ", 1)[1])
            if len(parts) == 3:
                assert parts.pop(0) == self.current_database
            assert len(parts) == 2, sql
            pair = (parts[0].replace('""', '"'), parts[1].replace('""', '"'))
            self.read_pairs.append(pair)
            return [{"rows_seen": self.entries[pair][2], "max_event_time": None}]
        if self.old and "current_user AS name" in sql and "rolbypassrls" in sql:
            self.asked.append(sql)
            raise KernelError('column "rolbypassrls" does not exist', "42703")
        if "AS version_num" in sql:
            self.asked.append(sql)
            return [{"version_num": 90426}]
        if "has_column_privilege(" in sql:
            self.asked.append(sql)
            grants = self.entries[pair][1]
            return [
                {"name": name, "ok": "SELECT" in grants or f"SELECT({name})" in grants}
                for name in ("date", "secret")
            ]
        rows = super().__call__(sql)
        if "has_table_privilege(" in sql:
            owner = self.entries[pair][0]
            rows[0].update(owner_name=owner, owns_mart=owner == "gate")
        return rows


def part_scope(mart: str) -> EnvironmentQuestions:
    return EnvironmentQuestions(
        tables=[TableQuestion(table=mart, event_time_column="date", columns={"date": "date"})]
    )


@pytest.mark.parametrize("engine", [*ENGINES, "clickhouse"])
@pytest.mark.parametrize("mart,rejected,literal", PART_NAMES)
def test_two_part_name_refuses_before_reading(
    engine: str, mart: str, rejected: str, literal: str
) -> None:
    catalog = PartCatalog(engine, mart)
    with pytest.raises(UnsafeIdentifierError) as caught:
        collect(catalog, part_scope(mart), engine=engine, target="part-test")
    assert str(caught.value) == f"unsafe identifier: {rejected!r}"
    assert not catalog.read_pairs
    assert not any("AS rows_seen" in sql for sql in catalog.asked)
    if engine != "clickhouse":
        assert catalog.catalog_pairs
        for site in SITES:
            if site == "format_type(":
                continue  # This question follows COUNT/MAX.
            asked = [sql for sql in catalog.asked if site in sql]
            assert asked, site
            assert all(literal in sql for sql in asked)


@pytest.mark.parametrize("engine", ENGINES)
def test_two_part_name_refuses_on_94(engine: str) -> None:
    catalog = PartCatalog(engine, "a.b.c", old=True)
    with pytest.raises(UnsafeIdentifierError, match=r"'b[.]c'"):
        collect(catalog, part_scope("a.b.c"), engine=engine, target="part-test")
    assert not catalog.read_pairs
    assert not any("pg_policy" in sql or "relrowsecurity" in sql for sql in catalog.asked)


@pytest.mark.parametrize(
    "engine,absent",
    [(engine, absent) for engine in ENGINES for absent in (False, True)] + [("clickhouse", True)],
)
@pytest.mark.parametrize("mart,rejected,literal", PART_NAMES)
def test_two_part_name_keeps_early_answer(
    engine: str, mart: str, rejected: str, literal: str, absent: bool
) -> None:
    catalog = PartCatalog(engine, mart)
    catalog.present = not absent
    catalog.readable = False
    facts = collect(catalog, part_scope(mart), engine=engine, target="part-test")
    assert facts.tables[0].exists is (not absent)
    assert facts.tables[0].readable is False
    assert not catalog.read_pairs
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


@pytest.mark.parametrize("engine", [*ENGINES, "clickhouse"])
def test_name_without_dot_keeps_absence(engine: str) -> None:
    catalog = PartCatalog(engine, "sales")
    catalog.present = False
    facts = collect(catalog, part_scope("sales"), engine=engine, target="part-test")
    presence = [
        read_literals(sql, engine)[1]
        for sql in catalog.asked
        if "FROM pg_tables" in sql or "FROM system.tables" in sql
    ]
    assert presence == [["sales", ""] * (1 if engine == "clickhouse" else 2)]
    assert len(facts.tables) == 1
    assert facts.tables[0].exists is False
    assert facts.tables[0].readable is False
    assert not catalog.read_pairs
    assert not any("AS rows_seen" in sql for sql in catalog.asked)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("mart,rejected,literal", PART_NAMES)
def test_two_part_catalog_literals_at_all_sites(
    monkeypatch: pytest.MonkeyPatch, engine: str, mart: str, rejected: str, literal: str
) -> None:
    # Reach the type question as in test_catalog_literals; refusal has separate cases.
    def allowed(*_parts: object) -> str:
        return literal[1:-1]

    monkeypatch.setattr(probe, "_qualified", allowed)
    catalog = PartCatalog(engine, mart)
    collect(catalog, part_scope(mart), engine=engine, target="part-test")
    for site in SITES:
        asked = [sql for sql in catalog.asked if site in sql]
        assert asked, site
        assert all(literal in sql for sql in asked)
    assert catalog.read_pairs == [catalog.exact_pair]
