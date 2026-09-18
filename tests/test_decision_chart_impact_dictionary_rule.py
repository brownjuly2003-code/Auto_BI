"""Dictionary-rule defects F-T-07-11 .. F-T-07-14 and repair F-T-10-1 .. F-T-10-5.

A scalar subquery WITH (`WITH (SELECT …) AS alias`) whose alias is read as a
bare column is a scope the statement runs, even when FROM never names it. A
named CTE — including a parenthesised body `WITH x AS ((SELECT …))` — is
reachable only through FROM/JOIN. The typed `dictGet` family is a closed named
set, so a hierarchy call stays a clause. An unnameable dictionary call is
worded as one this run could not name. The scope tree is built only when a
`dict…` call exists to place.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import sqlglot

import auto_bi.decision.impact as impact_mod
from auto_bi.decision.impact import (
    ChartRelation,
    _dictionary_reads_in,
    _DictionaryReads,
    _projection_star_in_reachable,
)
from tests.test_decision_chart_impact import (
    DASHBOARD,
    _reformulated,
    _relabelled,
    dashboard,
    impact_of,
    operator_dashboard,
    resolved,
)
from tests.test_decision_chart_impact_dictionary import TILE, charts, field_change, gaps

UNNAMED_CLAUSE = "whose dictionary read this run could not name"


# --- F-T-07-11: scalar subquery WITH whose alias is a bare column --------------------------


def test_a_dictionary_call_in_a_referenced_scalar_subquery_with_is_read(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1))) AS r "
        "SELECT sum(revenue) * r FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert report.chart_gaps == []


def test_a_dictionary_call_in_an_unreferenced_scalar_subquery_with_changes_nothing(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH (SELECT dictGet('dm.rev', 'revenue', toUInt64(1))) AS r "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_star_in_a_referenced_scalar_subquery_with_is_a_partial_gap(tmp_path: Path) -> None:
    sql = "WITH (SELECT * FROM dm.stores) AS m SELECT sum(revenue) * m FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


def test_a_star_in_an_unreferenced_scalar_subquery_with_is_not_a_partial_gap(
    tmp_path: Path,
) -> None:
    sql = "WITH (SELECT * FROM dm.stores) AS m SELECT sum(revenue) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


def test_projection_star_helper_agrees_on_the_scalar_subquery_with_forms() -> None:
    read = sqlglot.parse_one(
        "WITH (SELECT * FROM dm.stores) AS m SELECT sum(revenue) * m FROM dm.sales_daily",
        read="clickhouse",
    )
    unread = sqlglot.parse_one(
        "WITH (SELECT * FROM dm.stores) AS m SELECT sum(revenue) FROM dm.sales_daily",
        read="clickhouse",
    )
    assert _projection_star_in_reachable(read) is True
    assert _projection_star_in_reachable(unread) is False


def test_a_dictionary_read_in_a_referenced_scalar_subquery_with_keeps_the_metric(
    tmp_path: Path,
) -> None:
    """The hole this slice exists to close: a term column read through the dictionary."""
    sql = (
        "WITH (SELECT dictGet('dm.sales_daily', 'revenue', toUInt64(1))) AS r "
        "SELECT sum(store_id) * r FROM dm.sales_daily"
    )
    _, report = resolved(
        tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: operator_dashboard(sql)}
    )
    assert gaps(report) == [("chart.metric_unread", TILE)]


# --- F-T-07-12: named typed family; hierarchy stays a clause -------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "dictGetDecimal64('dm.rev', 'revenue', region)",
        "dictGetDate32('dm.rev', 'revenue', region)",
        "dictGetDateTime64('dm.rev', 'revenue', region)",
        "dictGetUInt128('dm.rev', 'revenue', region)",
        "dictGetUInt256('dm.rev', 'revenue', region)",
        "dictGetInt256('dm.rev', 'revenue', region)",
        "dictGetDecimal256('dm.rev', 'revenue', region)",
        "dictGetBool('dm.rev', 'revenue', region)",
        "dictGetAll('dm.rev', 'revenue', region)",
        "dictGetFloat32OrNull('dm.rev', 'revenue', region)",
        "dictGetDecimal128OrDefault('dm.rev', 'revenue', region, 0)",
        "dictGetIPv6OrNull('dm.rev', 'revenue', region)",
    ],
)
def test_a_named_typed_attribute_reader_resolves_the_read(tmp_path: Path, call: str) -> None:
    _, report = field_change(tmp_path, f"SELECT {call} FROM dm.sales_daily", "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


@pytest.mark.parametrize(
    "call",
    [
        "dictGetHierarchy('dm.rev', region)",
        "dictGetChildren('dm.rev', region)",
        "dictGetDescendants('dm.rev', region, 1)",
    ],
)
def test_widening_the_typed_family_does_not_swallow_hierarchy_calls(
    tmp_path: Path, call: str
) -> None:
    name = call.split("(", 1)[0]
    sql = f"SELECT sum(revenue), {call} FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert report.chart_gaps[0].detail == (
        f"operator SQL was not fully resolved: it calls {name}(...), {UNNAMED_CLAUSE}"
    )


# --- F-T-07-13: clause text is true for every unnameable call ------------------------------


def test_a_dictionary_call_with_a_named_attribute_and_unnamed_dictionary_uses_true_wording(
    tmp_path: Path,
) -> None:
    sql = "SELECT sum(revenue), dictGet(dict_name, 'revenue', region) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert report.chart_gaps[0].detail == (
        f"operator SQL was not fully resolved: it calls dictGet(...), {UNNAMED_CLAUSE}"
    )


def test_a_membership_test_uses_the_same_true_wording(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue), dictHas('dm.rev', region) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert report.chart_gaps[0].detail == (
        f"operator SQL was not fully resolved: it calls dictHas(...), {UNNAMED_CLAUSE}"
    )


# --- F-T-07-14: no dict prefix → no scope tree ---------------------------------------------


def test_dictionary_reads_do_not_build_a_scope_tree_when_there_is_no_dict_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sqlglot.parse_one(
        "WITH toUInt64(1) AS r SELECT sum(revenue) FROM dm.sales_daily",
        read="clickhouse",
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("build_scope must not run for a statement with no dict call")

    monkeypatch.setattr(impact_mod, "build_scope", forbidden)
    assert _dictionary_reads_in(root) == _DictionaryReads()


def test_a_dict_call_still_builds_a_scope_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    real = impact_mod.build_scope

    def wrapped(root: object) -> object:
        called.append(True)
        return real(root)

    monkeypatch.setattr(impact_mod, "build_scope", wrapped)
    root = sqlglot.parse_one(
        "SELECT dictGet('dm.rev', 'revenue', region) FROM dm.sales_daily",
        read="clickhouse",
    )
    reads = _dictionary_reads_in(root)
    assert called
    assert reads.reads == (("dm.rev", "revenue"),)


def test_a_tile_with_no_dictionary_call_does_not_log_a_sqlglot_scope_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="sqlglot")
    root = sqlglot.parse_one(
        "WITH toUInt64(1) AS r SELECT sum(revenue) FROM dm.sales_daily",
        read="clickhouse",
    )
    assert _dictionary_reads_in(root) == _DictionaryReads()
    assert not any("Cannot traverse scope" in record.message for record in caplog.records)


# --- false-reject: existing answers this slice must not take away --------------------------


def test_a_referenced_scalar_expr_with_still_reads(tmp_path: Path) -> None:
    sql = (
        "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
        "SELECT sum(revenue) * r FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [
        ("operator_table", ChartRelation.READS_FIELD, "query reads dm.rev.revenue")
    ]
    assert not any("dictGet" in gap.detail for gap in report.chart_gaps)


def test_an_unreferenced_scalar_expr_with_still_changes_nothing(tmp_path: Path) -> None:
    sql = (
        "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
        "SELECT sum(revenue) FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


def test_a_star_in_an_unread_regular_cte_is_still_not_a_hole(tmp_path: Path) -> None:
    sql = "WITH x AS (SELECT * FROM dm.stores) SELECT sum(revenue) FROM dm.sales_daily"
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


def test_a_cte_named_in_from_still_reads_its_dictionary_call(tmp_path: Path) -> None:
    sql = (
        "WITH x AS (SELECT dictGet('dm.rev', 'revenue', region) AS r FROM dm.sales_daily) "
        "SELECT r FROM x"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, sum(revenue) FROM dm.sales_daily GROUP BY region",
        "SELECT count(*), sum(revenue) FROM dm.sales_daily",
        "SELECT formatDateTime(date, '%Y'), sum(revenue) FROM dm.sales_daily GROUP BY 1",
        "SELECT toStartOfInterval(date, INTERVAL 1 DAY), sum(revenue) FROM dm.sales_daily "
        "GROUP BY 1",
        "SELECT dictionary_id, sum(revenue) FROM dm.sales_daily GROUP BY dictionary_id",
        "WITH toUInt64(1) AS r SELECT sum(revenue) FROM dm.sales_daily",
    ],
)
def test_ordinary_sql_still_reads_in_whole(tmp_path: Path, sql: str) -> None:
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []


def test_a_root_star_and_unparseable_text_keep_their_gaps(tmp_path: Path) -> None:
    _, starred = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard("SELECT * FROM dm.sales_daily")},
    )
    assert gaps(starred) == [("chart.sql_partial", TILE)]
    _, opaque = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard("this is not SQL")},
    )
    assert gaps(opaque) == [("chart.opaque_sql", TILE)]


def test_an_ir_tile_still_computes_the_metric(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate=_reformulated(), dashboards={DASHBOARD: dashboard()})
    impact = impact_of(report, "metric.formula_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("margin_by_region", ChartRelation.COMPUTES_METRIC)
    ]


def test_an_ir_tile_still_reads_a_field(tmp_path: Path) -> None:
    _, report = resolved(tmp_path, candidate={"semantic": _relabelled(1, 0, "Store surrogate key")})
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("stores_covered", ChartRelation.READS_FIELD)
    ]


# --- F-T-10-1: CTE plus VALUES / LATERAL / UNNEST must not crash ---------------------------
# `numbers()` never builds a non-Query scope; VALUES / LATERAL / UNNEST do, and
# pre-repair `_cte_scopes_read_as_columns` raised AttributeError on them.

CTE_PLUS_STAR = [
    (
        "values",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) "
        "SELECT * FROM VALUES('x UInt8', (1), (2)) WHERE c > 0",
    ),
    (
        "lateral",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) "
        "SELECT * FROM dm.sales_daily AS a, LATERAL (SELECT * FROM dm.stores) AS b",
    ),
    (
        "unnest",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) SELECT * FROM UNNEST([1, 2]) AS t(x)",
    ),
]

CTE_PLUS_DICT = [
    (
        "values",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) "
        "SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) FROM VALUES('x UInt8', (1), (2))",
    ),
    (
        "lateral",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) "
        "SELECT dictGet('dm.rev', 'revenue', region) FROM dm.sales_daily AS a, "
        "LATERAL (SELECT 1 FROM dm.stores) AS b",
    ),
    (
        "unnest",
        "WITH c AS (SELECT 1 AS v FROM dm.stores) "
        "SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) FROM UNNEST([1, 2]) AS t(x)",
    ),
]


@pytest.mark.parametrize(
    "sql",
    [sql for _, sql in CTE_PLUS_STAR],
    ids=[label for label, _ in CTE_PLUS_STAR],
)
def test_a_cte_plus_udtf_source_with_a_star_is_a_partial_gap_not_a_crash(
    tmp_path: Path, sql: str
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is True
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


@pytest.mark.parametrize(
    "sql",
    [sql for _, sql in CTE_PLUS_DICT],
    ids=[label for label, _ in CTE_PLUS_DICT],
)
def test_a_cte_plus_udtf_source_with_a_dictionary_call_is_read_not_a_crash(
    tmp_path: Path, sql: str
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _dictionary_reads_in(root).reads == (("dm.rev", "revenue"),)
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert [(chart, relation) for chart, relation, _ in charts(report)] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]


# --- F-T-10-2 / F-T-10-5: named CTE is reachable only through FROM/JOIN --------------------
#
# A parenthesised body `WITH x AS ((SELECT …))` is still a named CTE: sqlglot
# wraps it in Subquery, but does not set `scalar`. A coincidental column of the
# same name is not a reference to the CTE.


@pytest.mark.parametrize(
    "sql",
    [
        "WITH x AS (SELECT * FROM dm.other) SELECT x FROM dm.sales_daily",
        "WITH x AS ((SELECT * FROM dm.other)) SELECT x FROM dm.sales_daily",
    ],
    ids=["named", "parenthesised-named"],
)
def test_a_named_cte_whose_alias_is_a_coincidental_column_is_not_a_star_hole(
    tmp_path: Path, sql: str
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is False
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert report.chart_gaps == []


@pytest.mark.parametrize(
    "sql",
    [
        (
            "WITH x AS (SELECT dictGet('dm.rev', 'revenue', region) AS a FROM dm.other) "
            "SELECT x FROM dm.sales_daily"
        ),
        (
            "WITH x AS ((SELECT dictGet('dm.rev', 'revenue', region) AS a FROM dm.other)) "
            "SELECT x FROM dm.sales_daily"
        ),
    ],
    ids=["named", "parenthesised-named"],
)
def test_a_named_cte_whose_alias_is_a_coincidental_column_does_not_read_its_dictionary(
    tmp_path: Path, sql: str
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _dictionary_reads_in(root) == _DictionaryReads()
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []
    assert report.chart_gaps == []


# --- F-T-10-14: the star path is not symmetric with the dictionary path --------------------
#
# `_projection_star_in_reachable` tests reachability for query-bodied scopes
# only, so a star inside the expression form `WITH <expr> AS alias` counts
# whether or not the alias is read, while a dictionary call in the same place
# follows its alias. One gap more than the statement justifies hides nothing;
# the durable spec says so rather than promising the symmetry, and T-11 carries
# the code half.


@pytest.mark.parametrize(
    "sql",
    [
        "WITH tuple(*) AS t SELECT sum(revenue) FROM dm.sales_daily",
        "WITH tuple(*) AS t SELECT t, sum(revenue) FROM dm.sales_daily",
    ],
    ids=["alias-unread", "alias-read"],
)
def test_a_star_in_an_expression_with_counts_whether_or_not_its_alias_is_read(
    tmp_path: Path, sql: str
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is True
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert gaps(report) == [("chart.sql_partial", TILE)]


@pytest.mark.parametrize(
    "sql,expected",
    [
        (
            "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
            "SELECT sum(revenue) FROM dm.sales_daily",
            _DictionaryReads(),
        ),
        (
            "WITH dictGet('dm.rev', 'revenue', toUInt64(1)) AS r "
            "SELECT r, sum(revenue) FROM dm.sales_daily",
            _DictionaryReads(reads=(("dm.rev", "revenue"),)),
        ),
    ],
    ids=["alias-unread", "alias-read"],
)
def test_a_dictionary_call_in_an_expression_with_follows_its_alias(
    sql: str, expected: _DictionaryReads
) -> None:
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == expected
