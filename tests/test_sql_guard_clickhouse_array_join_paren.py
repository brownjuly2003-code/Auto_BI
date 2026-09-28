"""Parenthesized ClickHouse ARRAY JOIN items keep their expression semantics (T-25).

sqlglot 30.14.0 represents `(tags AS t)` as a ``Subquery`` around an expression,
which sends the item through query scoping even though ClickHouse reads it as an
ARRAY JOIN alias. A parenthesized SELECT is a real subquery and keeps its current
reads; the tests pin that HEAD result explicitly.
"""

from __future__ import annotations

from pathlib import Path

from auto_bi.agent.sql_guard import TableRef, extract_column_refs, extract_table_refs
from auto_bi.decision.impact import ChartRelation
from auto_bi.semantic.model import Column, ColumnRole
from tests.test_decision_chart_impact import DASHBOARD
from tests.test_decision_chart_impact_dictionary import charts, field_change, gaps
from tests.test_semantic_diff import snap
from tests.test_semantic_gateway import model
from tests.test_verified_assets import asset, codes, query, repository


def _attributed(sql: str) -> list[str]:
    reads = extract_column_refs(sql, dialect="clickhouse")
    return sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed)


def _tags_model():
    semantic = model()
    semantic.tables[0].columns.append(
        Column(name="tags", type="Array(String)", role=ColumnRole.DIMENSION)
    )
    return semantic


def _ungoverned_codes(tmp_path: Path, sql: str) -> list[str]:
    base = snap(tmp_path, "base", semantic=_tags_model())
    return codes(repository(tmp_path / "repo", asset(reference_query=query(sql))).ungoverned(base))


def test_parenthesized_array_join_expressions_are_aliases_and_real_columns_only() -> None:
    cases = {
        "SELECT t FROM dm.sales_daily ARRAY JOIN (tags AS t)": ["dm.sales_daily.tags"],
        "SELECT a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a": ["dm.sales_daily.tags"],
        "SELECT t, a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a": [
            "dm.sales_daily.t",
            "dm.sales_daily.tags",
        ],
        "SELECT tags FROM dm.sales_daily ARRAY JOIN (tags)": ["dm.sales_daily.tags"],
        "SELECT a FROM dm.sales_daily ARRAY JOIN (tags) AS a": ["dm.sales_daily.tags"],
        "SELECT t FROM dm.sales_daily LEFT ARRAY JOIN (tags AS t)": ["dm.sales_daily.tags"],
        "SELECT t FROM dm.sales_daily ARRAY JOIN ((tags AS t))": ["dm.sales_daily.tags"],
        "SELECT t, a, b FROM dm.sales_daily ARRAY JOIN ((tags AS t) AS a) AS b": [
            "dm.sales_daily.a",
            "dm.sales_daily.t",
            "dm.sales_daily.tags",
        ],
        'SELECT "A" FROM dm.sales_daily ARRAY JOIN (tags AS t) AS "A"': ["dm.sales_daily.tags"],
        "SELECT a FROM dm.sales_daily ARRAY JOIN ((tags)) AS a": ["dm.sales_daily.tags"],
        "SELECT y FROM dm.sales_daily ARRAY JOIN tags AS x, (tags AS y)": ["dm.sales_daily.tags"],
        "SELECT m FROM dm.sales_daily ARRAY JOIN (arrayMap(x -> upper(x), tags) AS m)": [
            "dm.sales_daily.tags"
        ],
    }
    for sql, expected in cases.items():
        reads = extract_column_refs(sql, dialect="clickhouse")
        assert _attributed(sql) == expected
        assert reads.unattributed == frozenset()


def test_parenthesized_real_subquery_keeps_head_reads() -> None:
    for item in ("(SELECT ['p','q'])", "((SELECT ['p','q']))"):
        reads = extract_column_refs(
            f"SELECT a FROM dm.sales_daily ARRAY JOIN {item} AS a",
            dialect="clickhouse",
        )

        # Observed at HEAD with sqlglot 30.14.0: the outer `a` is ambiguous against
        # dm.sales_daily, while the literal-only subquery contributes no physical read.
        assert reads.attributed == frozenset()
        assert reads.unattributed == frozenset({"a"})
        assert reads.starred is False


def test_array_join_normalization_reaches_table_refs_and_in_alias_resolution() -> None:
    expected = frozenset({TableRef(name="dm.sales_daily", quoted=False)})
    for sql in (
        "SELECT a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a WHERE region IN a",
        "SELECT t FROM dm.sales_daily ARRAY JOIN (tags AS t) WHERE region IN t",
    ):
        assert extract_table_refs(sql, dialect="clickhouse") == expected


def test_array_join_aliases_are_governed_and_inner_alias_is_discarded(tmp_path: Path) -> None:
    clean = [
        "SELECT t FROM dm.sales_daily ARRAY JOIN (tags AS t)",
        "SELECT a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a",
    ]
    for index, sql in enumerate(clean):
        assert _ungoverned_codes(tmp_path / f"clean_{index}", sql) == []

    sql = "SELECT t, a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a"
    base = snap(tmp_path / "table_column", "base", semantic=_tags_model())
    findings = repository(
        tmp_path / "table_column" / "repo", asset(reference_query=query(sql))
    ).ungoverned(base)
    assert codes(findings) == ["asset.query_ungoverned_field"]
    assert "dm.sales_daily.t" in " | ".join(item.detail for item in findings)


def test_array_join_aliases_resolve_chart_impact_and_cte_scope(tmp_path: Path) -> None:
    sql = "SELECT a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a"
    _, report = field_change(tmp_path / "alias", sql, "dm.sales_daily.tags")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.tags")
    ]
    assert gaps(report) == []

    sql = "SELECT t FROM dm.sales_daily ARRAY JOIN (tags AS t)"
    _, report = field_change(tmp_path / "alias_column", sql, "dm.sales_daily.t")
    assert charts(report) == []
    assert gaps(report) == []

    sql = (
        "WITH vals AS (SELECT region FROM dm.sales_daily) "
        "SELECT a FROM dm.sales_daily ARRAY JOIN (tags AS t) AS a WHERE region IN vals"
    )
    reads = extract_column_refs(sql, dialect="clickhouse")
    assert _attributed(sql) == ["dm.sales_daily.region", "dm.sales_daily.tags"]
    assert reads.unattributed == frozenset()
    _, report = field_change(tmp_path / "cte", sql, "dm.sales_daily.tags")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.sales_daily.tags")
    ]
    assert gaps(report) == [("chart.sql_partial", f"{DASHBOARD}#operator_table")]


def test_postgres_parentheses_keep_existing_column_reads() -> None:
    sql = "SELECT region FROM dm.sales_daily WHERE region IN (SELECT region FROM dm.sales_daily)"
    reads = extract_column_refs(sql, dialect="postgres")
    assert sorted(f"{ref.table.name}.{ref.name}" for ref in reads.attributed) == [
        "dm.sales_daily.region"
    ]
