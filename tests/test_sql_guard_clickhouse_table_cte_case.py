"""ClickHouse table CTE exemptions preserve identifier case."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import (
    SQLGuardError,
    extract_table_refs,
    guard_sql,
)
from auto_bi.auth import forbidden_tables
from auto_bi.ir.spec import ChartQuery, ChartSpec, DashboardSpec, Viz
from tests.test_sql_guard_clickhouse_alias import ungoverned_detail, ungoverned_pairs


@pytest.mark.parametrize(
    ("sql", "dialect", "expected"),
    [
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM a",
            "clickhouse",
            {"a"},
        ),
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM A",
            "clickhouse",
            set(),
        ),
        (
            'WITH "A" AS (SELECT 1) SELECT count(*) FROM "a"',
            "clickhouse",
            {"a"},
        ),
        (
            'WITH "A" AS (SELECT 1) SELECT count(*) FROM "A"',
            "clickhouse",
            set(),
        ),
        (
            "WITH a AS (SELECT 1) SELECT count(*) FROM A",
            "clickhouse",
            {"A"},
        ),
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM dm.a",
            "clickhouse",
            {"dm.a"},
        ),
        (
            "WITH Foo AS (SELECT 1) SELECT count(*) FROM foo",
            "postgres",
            set(),
        ),
    ],
    ids=[
        "mixed-case-mismatch",
        "exact-case",
        "quoted-mismatch",
        "quoted-exact-case",
        "reversed-case-mismatch",
        "qualified-physical-table",
        "postgres-folds-unquoted",
    ],
)
def test_table_extraction_respects_cte_case(sql: str, dialect: str, expected: set[str]) -> None:
    assert {ref.name for ref in extract_table_refs(sql, dialect=dialect)} == expected


@pytest.mark.parametrize(
    ("sql", "dialect", "rejects"),
    [
        (
            "WITH MeRgE AS (SELECT 1) SELECT * FROM merge",
            "clickhouse",
            True,
        ),
        (
            'WITH "MeRgE" AS (SELECT 1) SELECT * FROM merge',
            "clickhouse",
            True,
        ),
        (
            "WITH merge AS (SELECT 1) SELECT * FROM MeRgE",
            "clickhouse",
            True,
        ),
        (
            "WITH MeRgE AS (SELECT 1) SELECT * FROM MeRgE",
            "clickhouse",
            False,
        ),
        (
            "WITH merge AS (SELECT 1) SELECT * FROM dm.merge",
            "clickhouse",
            False,
        ),
        (
            "WITH MeRgE AS (SELECT 1) SELECT * FROM merge",
            "postgres",
            False,
        ),
    ],
    ids=[
        "mixed-case-denylist-bypass",
        "quoted-denylist-bypass",
        "reversed-case-denylist-bypass",
        "exact-case-cte",
        "qualified-physical-table",
        "postgres-folds-unquoted",
    ],
)
def test_guard_denylisted_table_name_requires_clickhouse_exact_cte_case(
    sql: str, dialect: str, rejects: bool
) -> None:
    if rejects:
        with pytest.raises(SQLGuardError, match=r"forbidden table function in SQL: merge\(\)"):
            guard_sql(sql, dialect=dialect)
    else:
        guard_sql(sql, dialect=dialect)


@pytest.mark.parametrize(
    ("sql", "engine", "expected"),
    [
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM a",
            "clickhouse",
            [("asset.query_ungoverned", "review")],
        ),
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM A",
            "clickhouse",
            [],
        ),
        (
            "WITH Foo AS (SELECT 1) SELECT count(*) FROM foo",
            "postgres",
            [],
        ),
    ],
    ids=["case-mismatch-is-ungoverned", "exact-case-is-governed", "postgres-control"],
)
def test_count_star_governance_uses_case_sensitive_clickhouse_cte_exemption(
    tmp_path: Path, sql: str, engine: str, expected: list[tuple[str, str]]
) -> None:
    assert ungoverned_pairs(tmp_path, sql, engine=engine) == expected
    if expected:
        assert "reads a," in ungoverned_detail(tmp_path / "detail", sql, engine=engine)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM a",
            ["a"],
        ),
        (
            "WITH A AS (SELECT 1) SELECT count(*) FROM A",
            [],
        ),
    ],
    ids=["case-mismatch-is-forbidden", "exact-case-is-allowed"],
)
def test_raw_sql_rbac_sees_case_sensitive_cte_table_reference(
    sql: str, expected: list[str]
) -> None:
    spec = DashboardSpec(
        title="raw",
        charts=[
            ChartSpec(
                id="raw",
                title="raw",
                viz=Viz.TABLE,
                query=ChartQuery(table="dm.sales_daily", raw_sql=sql),
            )
        ],
    )
    assert forbidden_tables(spec, ["dm"]) == expected
