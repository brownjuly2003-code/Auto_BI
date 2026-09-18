"""Dictionary-rule defects F-T-07-11 .. F-T-07-14 and repair F-T-10-1 .. F-T-10-5, T-11.

A scalar WITH (`WITH <expr> AS alias` and `WITH (SELECT …) AS alias`) is live
only when a column naming its alias sits in a part of the statement that
already runs: a bare column, or a column whose leftmost qualifier names it
(`t.1`, `t.name`, `t.a.b`). A named CTE — including a parenthesised body
`WITH x AS ((SELECT …))` — is reachable only through FROM/JOIN. A query nested
inside a scalar WITH body is part of that WITH: it runs when the WITH is live.
The star path uses the same predicate. The typed `dictGet` family is a closed
named set, so a hierarchy call stays a clause. An unnameable dictionary call
is worded as one this run could not name. The reachability walk runs only when
a `dict…` call exists to place, or when a star must be placed.
"""

from __future__ import annotations

import logging
import time
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


# --- F-T-07-14: no dict prefix → no reachability walk --------------------------------------


def test_dictionary_reads_do_not_walk_reachability_when_there_is_no_dict_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sqlglot.parse_one(
        "WITH toUInt64(1) AS r SELECT sum(revenue) FROM dm.sales_daily",
        read="clickhouse",
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("reachability walk must not run for a statement with no dict call")

    monkeypatch.setattr(impact_mod, "_runs_in_statement", forbidden)
    assert _dictionary_reads_in(root) == _DictionaryReads()


def test_a_dict_call_still_decides_reachability(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    real = impact_mod._runs_in_statement

    def wrapped(root: object) -> object:
        called.append(True)
        return real(root)

    monkeypatch.setattr(impact_mod, "_runs_in_statement", wrapped)
    sql = (
        "WITH x AS (SELECT dictGet('dm.rev', 'cost', toUInt64(1)) AS r FROM dm.other) "
        "SELECT dictGet('dm.rev', 'revenue', toUInt64(1)) FROM dm.sales_daily"
    )
    reads = _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse"))
    assert called
    assert reads.reads == (("dm.rev", "revenue"),)
    assert reads.clauses == ()


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


# --- F-T-10-14 / T-11: the star path agrees with the dictionary path ------------------------
#
# A star inside the expression form `WITH <expr> AS alias` follows its alias
# the same way a dictionary call does: unread alias is not a hole, a running
# reader makes it one. `_projection_star_in_reachable` is now "a non-COUNT
# star for which `_runs_in_statement` is true".


@pytest.mark.parametrize(
    "sql,counts",
    [
        ("WITH tuple(*) AS t SELECT sum(revenue) FROM dm.sales_daily", False),
        ("WITH tuple(*) AS t SELECT t, sum(revenue) FROM dm.sales_daily", True),
    ],
    ids=["alias-unread", "alias-read"],
)
def test_a_star_in_an_expression_with_follows_its_alias(
    tmp_path: Path, sql: str, counts: bool
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is counts
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    if counts:
        assert gaps(report) == [("chart.sql_partial", TILE)]
    else:
        assert report.chart_gaps == []


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


# --- T-11: a scalar WITH is live only when a running part reads its alias -------------------
#
# F-T-10-13: a bare column naming the alias inside a CTE the statement never
# runs must not make the scalar WITH reachable. F-T-10-14: the star path uses
# the same predicate. Rows a–n are the orchestrator's T-12-tree probes.

_DICT_GET_REV = "dictGet('dm.rev', 'revenue', toUInt64(1))"
_READS_REV = (
    "operator_table",
    ChartRelation.READS_FIELD,
    "query reads dm.rev.revenue",
)


@pytest.mark.parametrize(
    "sql,reads",
    [
        (
            # a. subquery-form, reader only inside unread named CTE
            f"WITH (SELECT {_DICT_GET_REV}) AS r, x AS (SELECT r AS z FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # b. the same, but x is named in FROM so its reader runs
            f"WITH (SELECT {_DICT_GET_REV}) AS r, x AS (SELECT r AS z FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily, x",
            True,
        ),
        (
            # c. expression-form, reader only inside unread named CTE
            f"WITH {_DICT_GET_REV} AS r, x AS (SELECT r AS z FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # d. alias chain: a is read only by unread b
            f"WITH {_DICT_GET_REV} AS a, a + 1 AS b SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # e. alias chain: SELECT b makes a live
            f"WITH {_DICT_GET_REV} AS a, a + 1 AS b SELECT b, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            # g. subquery-form alias that reads a, itself selected
            f"WITH {_DICT_GET_REV} AS a, (SELECT a) AS s "
            "SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            # h. reader in the root ORDER BY
            f"WITH (SELECT {_DICT_GET_REV}) AS r "
            "SELECT sum(revenue) FROM dm.sales_daily ORDER BY r",
            True,
        ),
        (
            # h. reader in a WHERE subquery
            f"WITH (SELECT {_DICT_GET_REV}) AS r SELECT sum(revenue) FROM dm.sales_daily "
            "WHERE revenue > (SELECT r)",
            True,
        ),
        (
            # h. nested SELECT inside unread expression-form WITH
            f"WITH 1 + (SELECT {_DICT_GET_REV}) AS r SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # h. the same, alias selected
            f"WITH 1 + (SELECT {_DICT_GET_REV}) AS r SELECT r, sum(revenue) FROM dm.sales_daily",
            True,
        ),
    ],
    ids=[
        "a-dead-reader-subquery-form",
        "b-live-reader-via-named-cte",
        "c-dead-reader-expression-form",
        "d-unread-alias-chain",
        "e-live-alias-chain",
        "g-live-subquery-form-chain",
        "h-order-by-reader",
        "h-where-subquery-reader",
        "h-nested-select-unread",
        "h-nested-select-read",
    ],
)
def test_a_dictionary_call_follows_a_running_reader(tmp_path: Path, sql: str, reads: bool) -> None:
    expected = _DictionaryReads(reads=(("dm.rev", "revenue"),)) if reads else _DictionaryReads()
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == expected
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    if reads:
        assert charts(report) == [_READS_REV]
    else:
        assert charts(report) == []
        assert report.chart_gaps == []


def test_an_unread_subquery_alias_that_reads_an_expression_alias_does_not_read(
    tmp_path: Path,
) -> None:
    """f. `s` is unread, so `a` is not live. sql_guard may still name `a` unattributed."""
    sql = f"WITH {_DICT_GET_REV} AS a, (SELECT a) AS s SELECT sum(revenue) FROM dm.sales_daily"
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == _DictionaryReads()
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == []


@pytest.mark.parametrize(
    "sql,starred",
    [
        (
            # k. subquery-form star, reader only inside unread named CTE
            "WITH (SELECT * FROM dm.other) AS r, x AS (SELECT r AS z FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # l. expression-form star, reader only inside unread named CTE
            "WITH tuple(*) AS t, x AS (SELECT t AS z FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # m. star nested under unread expression-form WITH
            "WITH 1 + (SELECT count() FROM (SELECT * FROM dm.other)) AS r "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # n. subquery-form star, alias unread
            "WITH (SELECT * FROM dm.other) AS r SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # n. subquery-form star, alias read
            "WITH (SELECT * FROM dm.other) AS r SELECT r, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            # n. named CTE with a star, not named in FROM
            "WITH x AS (SELECT * FROM dm.other) SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # n. named CTE with a star, named in FROM
            "WITH x AS (SELECT * FROM dm.other) SELECT sum(revenue) FROM dm.sales_daily, x",
            True,
        ),
    ],
    ids=[
        "k-dead-reader-subquery-star",
        "l-dead-reader-expression-star",
        "m-nested-star-unread",
        "n-subquery-star-unread",
        "n-subquery-star-read",
        "n-named-cte-unread",
        "n-named-cte-in-from",
    ],
)
def test_a_star_follows_a_running_reader(tmp_path: Path, sql: str, starred: bool) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is starred
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    if starred:
        assert gaps(report) == [("chart.sql_partial", TILE)]
    else:
        assert report.chart_gaps == []


# --- F-T-11-1: a query nested in a live expression-form WITH is part of that WITH ----------
#
# sqlglot builds no scope for a query nested inside `WITH <expr> AS alias`, so a
# FROM that names a CTE there never opened a gate. The AST walk treats that
# nested query as part of the WITH: it runs when the alias is live. Each live
# row has a control where the outer alias is not selected; the control reads
# nothing and raises no gap. Rows a–n above keep their expected outcomes.


@pytest.mark.parametrize(
    "sql,reads",
    [
        (
            # (1) named CTE named from a subquery inside a live expression-form body
            f"WITH {_DICT_GET_REV} AS a, x AS (SELECT a AS z FROM dm.other), "
            "1 + (SELECT z FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH {_DICT_GET_REV} AS a, x AS (SELECT a AS z FROM dm.other), "
            "1 + (SELECT z FROM x) AS s SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # (2) nested WITH inside a live expression-form body
            f"WITH 1 + (WITH {_DICT_GET_REV} AS q SELECT q) AS s "
            "SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH 1 + (WITH {_DICT_GET_REV} AS q SELECT q) AS s "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # (4) named CTE named only from a subquery inside a live expression-form WITH
            f"WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other), "
            "1 + (SELECT v FROM x) AS r SELECT r, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other), "
            "1 + (SELECT v FROM x) AS r SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # (5) named CTE declared inside a live expression-form body, never named
            f"WITH 1 + (WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other) SELECT 1) AS s "
            "SELECT s, sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            f"WITH 1 + (WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other) "
            "SELECT v FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH 1 + (WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other) SELECT 1) AS s "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            f"WITH 1 + (WITH x AS (SELECT {_DICT_GET_REV} AS v FROM dm.other) "
            "SELECT v FROM x) AS s SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # (6) a named CTE named only from its own body stays closed
            f"WITH x AS (SELECT {_DICT_GET_REV} AS v FROM x) "
            "SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            # (7) case-insensitive: FROM x opens WITH X
            f"WITH X AS (SELECT {_DICT_GET_REV} AS v FROM dm.other) "
            "SELECT sum(revenue) FROM dm.sales_daily, x",
            True,
        ),
    ],
    ids=[
        "1-live-reader-via-subquery-in-expr-with",
        "1-control-outer-alias-unread",
        "2-live-nested-with-in-expr-with",
        "2-control-outer-alias-unread",
        "4-named-cte-from-subquery-in-expr-with",
        "4-control-outer-alias-unread",
        "5-inner-named-cte-never-named",
        "5-inner-named-cte-named",
        "5-control-never-named-outer-unread",
        "5-control-named-outer-unread",
        "6-self-reference-stays-closed",
        "7-case-insensitive-from-opens-named-cte",
    ],
)
def test_a_nested_query_inside_a_live_expression_with_follows_reachability(
    tmp_path: Path, sql: str, reads: bool
) -> None:
    expected = _DictionaryReads(reads=(("dm.rev", "revenue"),)) if reads else _DictionaryReads()
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == expected
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    if reads:
        assert charts(report) == [_READS_REV]
    else:
        assert charts(report) == []
        assert report.chart_gaps == []


@pytest.mark.parametrize(
    "sql,starred",
    [
        (
            # (3) star reached through a subquery inside a live expression-form WITH
            "WITH tuple(*) AS t, x AS (SELECT t AS z FROM dm.other), "
            "1 + (SELECT z FROM x) AS s SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            "WITH tuple(*) AS t, x AS (SELECT t AS z FROM dm.other), "
            "1 + (SELECT z FROM x) AS s SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            "WITH 1 + (WITH tuple(*) AS q SELECT q) AS s "
            "SELECT s, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            "WITH 1 + (WITH tuple(*) AS q SELECT q) AS s SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
    ],
    ids=[
        "3-star-via-subquery-in-expr-with",
        "3-control-outer-alias-unread",
        "3-star-in-nested-with-in-expr-with",
        "3-control-nested-with-outer-unread",
    ],
)
def test_a_star_reached_through_a_nested_query_inside_a_live_expression_with(
    tmp_path: Path, sql: str, starred: bool
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is starred
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    if starred:
        assert gaps(report) == [("chart.sql_partial", TILE)]
        assert "it selects *" in report.chart_gaps[0].detail
    else:
        assert report.chart_gaps == []


def test_a_shadowed_scalar_alias_is_a_known_over_report(tmp_path: Path) -> None:
    """F-T-11-3: shadowing is not modelled, so the inner `a` still opens the outer WITH.

    The inner `a` is the inner WITH's literal 1. The resolver still names a read of
    `dm.rev.revenue`. That over-reports and hides nothing.
    """
    sql = (
        f"WITH {_DICT_GET_REV} AS a "
        "SELECT sum(revenue) FROM (WITH 1 AS a SELECT revenue, a FROM dm.sales_daily)"
    )
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == _DictionaryReads(
        reads=(("dm.rev", "revenue"),)
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [_READS_REV]


# --- F-T-11-7: a tuple-element access reads the scalar alias --------------------------------
#
# `t.1` parses as Column(this=Literal 1, table=t). A qualified column whose table
# names the scalar alias is a reader, whatever its `this`. A table alias with the
# same name opens it too — a documented over-report.


@pytest.mark.parametrize(
    "sql,starred",
    [
        ("WITH tuple(*) AS t SELECT t.1, sum(revenue) FROM dm.sales_daily", True),
        ("WITH tuple(*) AS t SELECT sum(revenue) FROM dm.sales_daily", False),
        ("WITH tuple(*) AS t SELECT t.name, sum(revenue) FROM dm.sales_daily", True),
    ],
    ids=["tuple-element-read", "tuple-element-unread-control", "tuple-attr-read"],
)
def test_a_star_read_through_a_tuple_element_follows_its_alias(
    tmp_path: Path, sql: str, starred: bool
) -> None:
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is starred
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    if starred:
        assert gaps(report) == [("chart.sql_partial", TILE)]
        assert "it selects *" in report.chart_gaps[0].detail
    else:
        assert report.chart_gaps == []


@pytest.mark.parametrize(
    "sql,reads",
    [
        (
            f"WITH (SELECT {_DICT_GET_REV}, 2) AS t SELECT t.1, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH (SELECT {_DICT_GET_REV}, 2) AS t SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            f"WITH tuple({_DICT_GET_REV}, 2) AS t SELECT t.1, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH tuple({_DICT_GET_REV}, 2) AS t SELECT sum(revenue) FROM dm.sales_daily",
            False,
        ),
    ],
    ids=[
        "subquery-tuple-element-read",
        "subquery-tuple-element-unread-control",
        "expression-tuple-element-read",
        "expression-tuple-element-unread-control",
    ],
)
def test_a_dictionary_call_read_through_a_tuple_element_follows_its_alias(
    tmp_path: Path, sql: str, reads: bool
) -> None:
    expected = _DictionaryReads(reads=(("dm.rev", "revenue"),)) if reads else _DictionaryReads()
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == expected
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    if reads:
        assert charts(report) == [_READS_REV]
    else:
        assert charts(report) == []
        assert report.chart_gaps == []


def test_a_same_named_table_alias_opens_a_scalar_with_as_a_known_over_report(
    tmp_path: Path,
) -> None:
    """Documented over-report: `FROM dm.sales_daily AS t` also opens scalar `WITH … AS t`.

    `t.region` / `t.revenue` are columns of the table alias, not of the scalar WITH.
    Shadowing is not modelled, so the qualifier still opens the WITH; that hides nothing.
    """
    sql = (
        f"WITH (SELECT {_DICT_GET_REV}) AS t "
        "SELECT t.region, sum(t.revenue) FROM dm.sales_daily AS t"
    )
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == _DictionaryReads(
        reads=(("dm.rev", "revenue"),)
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [_READS_REV]


# --- F-T-11-13: a tuple access at any depth reads the scalar alias --------------------------
#
# sqlglot parks `t.a.b` as Column(this=b, table=a, db=t). The candidate alias is the
# leftmost written part, so db/catalog-qualified columns still open a scalar WITH.


@pytest.mark.parametrize(
    "access,starred",
    [
        ("t.a.b", True),
        ("t.a.1", True),
        ("t.a.b.c", True),
        ("u.a.b", False),
    ],
    ids=["two-level", "two-level-index", "three-level", "unread-control"],
)
def test_a_star_read_through_a_deeper_tuple_access_follows_its_alias(
    tmp_path: Path, access: str, starred: bool
) -> None:
    sql = f"WITH tuple(*) AS t SELECT {access}, sum(revenue) FROM dm.sales_daily"
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is starred
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    if starred:
        assert gaps(report) == [("chart.sql_partial", TILE)]
        assert "it selects *" in report.chart_gaps[0].detail
    else:
        assert all("it selects *" not in gap.detail for gap in report.chart_gaps)


@pytest.mark.parametrize(
    "sql,reads",
    [
        (
            f"WITH tuple({_DICT_GET_REV}, 2) AS t SELECT t.a.1, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH (SELECT {_DICT_GET_REV}, 2) AS t SELECT t.a.1, sum(revenue) FROM dm.sales_daily",
            True,
        ),
        (
            f"WITH tuple({_DICT_GET_REV}, 2) AS t SELECT u.a.1, sum(revenue) FROM dm.sales_daily",
            False,
        ),
        (
            f"WITH (SELECT {_DICT_GET_REV}, 2) AS t SELECT u.a.1, sum(revenue) FROM dm.sales_daily",
            False,
        ),
    ],
    ids=[
        "expression-two-level-read",
        "subquery-two-level-read",
        "expression-two-level-unread-control",
        "subquery-two-level-unread-control",
    ],
)
def test_a_dictionary_call_read_through_a_deeper_tuple_access_follows_its_alias(
    tmp_path: Path, sql: str, reads: bool
) -> None:
    expected = _DictionaryReads(reads=(("dm.rev", "revenue"),)) if reads else _DictionaryReads()
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == expected
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    if reads:
        assert charts(report) == [_READS_REV]
    else:
        assert charts(report) == []


def test_a_database_qualifier_opens_a_scalar_with_as_a_known_over_report(
    tmp_path: Path,
) -> None:
    """Documented over-report: `dm.sales_daily.revenue` also opens scalar `WITH … AS dm`."""
    sql = (
        f"WITH (SELECT {_DICT_GET_REV}) AS dm "
        "SELECT sum(dm.sales_daily.revenue) FROM dm.sales_daily"
    )
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == _DictionaryReads(
        reads=(("dm.rev", "revenue"),)
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [_READS_REV]


# --- F-T-11-14: nested query anywhere, and a subquery-form tuple-element star ---------------


def test_a_named_cte_read_from_a_nested_arrayjoin_subquery_is_reachable(tmp_path: Path) -> None:
    sql = (
        f"WITH x AS (SELECT {_DICT_GET_REV} AS a FROM dm.other) "
        "SELECT arrayJoin((SELECT groupArray(a) FROM x)) FROM dm.sales_daily"
    )
    assert _dictionary_reads_in(sqlglot.parse_one(sql, read="clickhouse")) == _DictionaryReads(
        reads=(("dm.rev", "revenue"),)
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert charts(report) == [_READS_REV]


def test_a_star_in_a_named_cte_read_from_a_nested_arrayjoin_subquery_is_a_gap(
    tmp_path: Path,
) -> None:
    sql = (
        "WITH x AS (SELECT * FROM dm.other) "
        "SELECT arrayJoin((SELECT groupArray(a) FROM x)) FROM dm.sales_daily"
    )
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is True
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


def test_a_tuple_element_reader_of_a_subquery_form_with_opens_its_star(tmp_path: Path) -> None:
    sql = "WITH (SELECT * FROM dm.other) AS r SELECT a FROM dm.sales_daily WHERE r.1 = 1"
    root = sqlglot.parse_one(sql, read="clickhouse")
    assert _projection_star_in_reachable(root) is True
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard(sql)},
    )
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


# --- F-T-11-8 / F-T-11-15: the gate walk is linear and computed once per SQL ----------------


def _cte_chain_sql(named: int, scalars: int) -> str:
    cols = ", ".join(f"col_{i}" for i in range(20))
    parts = [f"c0 AS (SELECT {cols} FROM dm.other)"]
    parts.extend(f"c{n} AS (SELECT {cols} FROM c{n - 1})" for n in range(1, named + 1))
    dict_n = scalars // 2
    for i in range(dict_n):
        attr = "revenue" if i == 0 else "cost"
        parts.append(f"(SELECT dictGet('dm.rev', '{attr}', toUInt64(1))) AS r{i}")
    parts.extend(f"tuple(*) AS t{i}" for i in range(scalars - dict_n))
    return f"WITH {', '.join(parts)} SELECT r0, t0, {cols} FROM c{named}"


def test_cte_gate_walk_on_a_long_chain_stays_linear() -> None:
    """A 120-CTE chain plus 30 scalar WITHs must not re-scan every gate per pass.

    A 4× sibling (480 named CTEs, 120 scalar WITHs) must stay under 8× the small
    size, so a return to HEAD-like superlinear cost fails even under the 2 s ceiling.
    """
    small_root = sqlglot.parse_one(_cte_chain_sql(120, 30), read="clickhouse")
    large_root = sqlglot.parse_one(_cte_chain_sql(480, 120), read="clickhouse")

    def once(root: sqlglot.Expression) -> tuple[float, _DictionaryReads, bool]:
        started = time.perf_counter()
        reads = _dictionary_reads_in(root)
        starred = _projection_star_in_reachable(root)
        return time.perf_counter() - started, reads, starred

    def best_of_two(root: sqlglot.Expression) -> tuple[float, _DictionaryReads, bool]:
        first = once(root)
        second = once(root)
        return first if first[0] <= second[0] else second

    small_t, small_reads, small_starred = best_of_two(small_root)
    large_t, large_reads, large_starred = best_of_two(large_root)
    assert small_t < 2.0, f"gate walk took {small_t:.3f}s on a 120-CTE chain"
    assert (
        large_t / small_t < 8
    ), f"4x chain took {large_t:.3f}s vs {small_t:.3f}s ({large_t / small_t:.2f}x)"
    expected = _DictionaryReads(reads=(("dm.rev", "revenue"),))
    assert small_reads == expected
    assert small_starred is True
    assert large_reads == expected
    assert large_starred is True


def test_dictionary_and_star_paths_share_one_cte_gate_walk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []
    real = impact_mod._open_cte_gates

    def wrapped(root: object) -> object:
        calls.append(1)
        return real(root)

    monkeypatch.setattr(impact_mod, "_open_cte_gates", wrapped)
    sql = (
        "WITH tuple(*) AS t "
        "SELECT t.1, dictGet('dm.rev', 'revenue', toUInt64(1)), sum(revenue) "
        "FROM dm.sales_daily"
    )
    _, report = field_change(tmp_path, sql, "dm.rev.revenue")
    assert len(calls) == 1
    assert charts(report) == [_READS_REV]
    assert gaps(report) == [("chart.sql_partial", TILE)]
    assert "it selects *" in report.chart_gaps[0].detail


def test_ordinary_sql_does_not_walk_cte_gates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("reachability walk must not run without a dict call or a star")

    monkeypatch.setattr(impact_mod, "_open_cte_gates", forbidden)
    _, report = resolved(
        tmp_path,
        candidate={"semantic": _relabelled(0, 4, "Revenue, net of VAT")},
        dashboards={DASHBOARD: operator_dashboard("SELECT sum(revenue) FROM dm.sales_daily")},
    )
    impact = impact_of(report, "field.description_changed")
    assert [(chart.chart, chart.relation) for chart in impact.charts] == [
        ("operator_table", ChartRelation.READS_FIELD)
    ]
    assert report.chart_gaps == []
