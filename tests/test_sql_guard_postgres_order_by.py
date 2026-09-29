"""PostgreSQL resolves whole sort names through outputs and expressions through inputs."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import (
    SQLGuardError,
    extract_column_refs,
    extract_table_names,
    guard_sql,
)
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def _reads(sql: str, *, dialect: str = "postgres") -> set[str]:
    refs = extract_column_refs(sql, dialect=dialect)
    return {f"{ref.table.name}.{ref.name}" for ref in refs.attributed}


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            'SELECT region AS r FROM dm.sales_daily ORDER BY r COLLATE "C"',
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT DISTINCT ON (r || '') region AS r FROM dm.sales_daily ORDER BY r || ''",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT DISTINCT ON (r || '') region AS r FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r FROM dm.sales_daily ORDER BY r + 1",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r FROM dm.sales_daily ORDER BY lower(r)",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
    ],
)
def test_sort_expression_reads_input(tmp_path: Path, sql: str, expected: set[str]) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "dm.sales_daily.r" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region AS R FROM dm.sales_daily ORDER BY r",
        'SELECT region AS R FROM dm.sales_daily ORDER BY "r"',
        "SELECT region AS R FROM dm.sales_daily ORDER BY (r)",
        "SELECT DISTINCT ON (r) region AS R FROM dm.sales_daily ORDER BY r",
        "SELECT DISTINCT ON (r) region AS R FROM dm.sales_daily",
        "SELECT upper(region) AS region FROM dm.sales_daily ORDER BY region",
    ],
)
def test_whole_sort_name_reads_output(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["count(*)", "(count(*))"])
@pytest.mark.parametrize("sort", ["count", "(count)", "COUNT", '"count"', "count DESC NULLS LAST"])
def test_implicit_count_sort_name_reads_output(tmp_path: Path, projected: str, sort: str) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY {sort}"
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.attributed and not refs.ambiguous and not refs.unattributed
    assert not refs.starred
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_implicit_count_sort_keeps_argument_read(tmp_path: Path) -> None:
    sql = "SELECT count(internal_cost_raw) FROM dm.sales_daily ORDER BY count"
    assert _reads(sql) == {"dm.sales_daily.internal_cost_raw"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "internal_cost_raw" in detail and "dm.sales_daily.count" not in detail


@pytest.mark.parametrize(
    "function, projected, sort",
    [
        ("avg", "avg(internal_cost_raw)", "avg"),
        ("min", "(min(internal_cost_raw))", "(min)"),
        ("max", "max(internal_cost_raw)", "max DESC NULLS LAST"),
    ],
)
def test_implicit_aggregate_sort_names_read_output_and_argument(
    tmp_path: Path, function: str, projected: str, sort: str
) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY {sort}"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.internal_cost_raw"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "internal_cost_raw" in detail and f"dm.sales_daily.{function}" not in detail


@pytest.mark.parametrize(
    "function, sort, expected",
    [
        ('"Avg"', '"Avg"', {"dm.sales_daily.revenue"}),
        ('"Avg"', "avg", {"dm.sales_daily.revenue", "dm.sales_daily.avg"}),
        ('"Min"', '"Min"', {"dm.sales_daily.revenue"}),
        ('"Min"', "min", {"dm.sales_daily.revenue", "dm.sales_daily.min"}),
        ('"Max"', '"Max"', {"dm.sales_daily.revenue"}),
        ('"Max"', "max", {"dm.sales_daily.revenue", "dm.sales_daily.max"}),
    ],
)
def test_quoted_aggregate_function_spelling_controls_output_name(
    tmp_path: Path, function: str, sort: str, expected: set[str]
) -> None:
    sql = f"SELECT {function}(revenue) FROM dm.sales_daily ORDER BY {sort}"
    assert _reads(sql) == expected
    expected_codes = [] if len(expected) == 1 else ["asset.query_ungoverned_field"]
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == expected_codes


@pytest.mark.parametrize("function", ["avg", "min", "max"])
def test_implicit_aggregate_distinct_on_reads_output(tmp_path: Path, function: str) -> None:
    sql = (
        f"SELECT DISTINCT ON ({function}) {function}(revenue) "
        f"FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("operator", ["UNION", "INTERSECT", "EXCEPT"])
@pytest.mark.parametrize("function", ["avg", "min", "max"])
def test_implicit_aggregate_names_set_output(tmp_path: Path, operator: str, function: str) -> None:
    sql = (
        f"SELECT {function}(revenue) FROM dm.sales_daily {operator} "
        f"SELECT {function}(revenue) FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT +avg(revenue) FROM dm.sales_daily ORDER BY avg",
        "SELECT avg(revenue) + 1 FROM dm.sales_daily ORDER BY avg",
        "SELECT avg(revenue) FROM dm.sales_daily AS s ORDER BY s.avg",
        "SELECT avg(revenue) FROM dm.sales_daily GROUP BY avg",
    ],
)
def test_aggregate_output_name_boundaries_remain_input_reads(tmp_path: Path, sql: str) -> None:
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.avg"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_implicit_sum_sort_name_reads_output(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue) FROM dm.sales_daily ORDER BY sum"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_windowed_implicit_sum_sort_name_reads_output(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue) OVER () FROM dm.sales_daily ORDER BY sum"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_windowed_implicit_aggregate_reads_window_inputs(tmp_path: Path, function: str) -> None:
    sql = (
        f"SELECT {function}(revenue) OVER (PARTITION BY region ORDER BY date) "
        f"FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.region",
        "dm.sales_daily.date",
    }
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_windowed_filtered_aggregate_reads_filter_and_window_inputs(tmp_path: Path) -> None:
    sql = (
        "SELECT sum(revenue) FILTER (WHERE region = 'north') "
        "OVER (PARTITION BY store_id ORDER BY date) "
        "FROM dm.sales_daily ORDER BY sum"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.sales_daily.date",
    }
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "SELECT DISTINCT ON (sum) sum(revenue) OVER (PARTITION BY region ORDER BY date) "
            "FROM dm.sales_daily ORDER BY sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.region", "dm.sales_daily.date"},
        ),
        (
            "SELECT sum(revenue) OVER () FROM dm.sales_daily UNION "
            "SELECT sum(revenue) OVER () FROM dm.sales_daily ORDER BY sum",
            {"dm.sales_daily.revenue"},
        ),
        (
            "SELECT (sum(revenue) OVER ()) FROM dm.sales_daily ORDER BY sum",
            {"dm.sales_daily.revenue"},
        ),
        ("SELECT sum(1) OVER () ORDER BY sum", set()),
    ],
)
def test_windowed_aggregate_output_name_boundaries(
    tmp_path: Path, sql: str, expected: set[str]
) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == expected
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "SELECT sum(revenue) OVER () AS total FROM dm.sales_daily ORDER BY sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
        (
            'SELECT sum(revenue) OVER () FROM dm.sales_daily ORDER BY "SUM"',
            {"dm.sales_daily.revenue", "dm.sales_daily.SUM"},
        ),
        (
            "SELECT +sum(revenue) OVER () FROM dm.sales_daily ORDER BY sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
        (
            "SELECT sum(revenue) OVER () FROM dm.sales_daily ORDER BY lower(sum)",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
        (
            "SELECT sum(revenue) OVER () FROM dm.sales_daily GROUP BY sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
    ],
)
def test_windowed_aggregate_output_name_boundaries_remain_input_reads(
    tmp_path: Path, sql: str, expected: set[str]
) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "sort, expected",
    [
        ('"Sum"', {"dm.sales_daily.revenue"}),
        ("sum", {"dm.sales_daily.revenue", "dm.sales_daily.sum"}),
    ],
)
def test_windowed_quoted_aggregate_function_spelling_controls_output_name(
    tmp_path: Path, sort: str, expected: set[str]
) -> None:
    sql = f'SELECT "Sum"(revenue) OVER () FROM dm.sales_daily ORDER BY {sort}'
    assert _reads(sql) == expected
    expected_codes = [] if len(expected) == 1 else ["asset.query_ungoverned_field"]
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == expected_codes


def test_filtered_sum_sort_name_reads_output(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue) FILTER (WHERE region = 'north') FROM dm.sales_daily ORDER BY sum"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_filtered_aggregate_sort_name_reads_output_and_filter(
    tmp_path: Path, function: str
) -> None:
    sql = (
        f"SELECT {function}(revenue) FILTER (WHERE region = 'north') "
        f"FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_filtered_aggregate_distinct_on_reads_output(tmp_path: Path, function: str) -> None:
    sql = (
        f"SELECT DISTINCT ON ({function}) {function}(revenue) "
        f"FILTER (WHERE region = 'north') FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("operator", ["UNION", "INTERSECT", "EXCEPT"])
@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_filtered_aggregate_names_set_output(tmp_path: Path, operator: str, function: str) -> None:
    sql = (
        f"SELECT {function}(revenue) FILTER (WHERE region = 'north') FROM dm.sales_daily "
        f"{operator} SELECT {function}(revenue) FILTER (WHERE region = 'north') "
        f"FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_parenthesized_filtered_aggregate_sort_name_reads_output(
    tmp_path: Path, function: str
) -> None:
    sql = (
        f"SELECT ({function}(revenue) FILTER (WHERE region = 'north')) "
        f"FROM dm.sales_daily ORDER BY {function}"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["count", "sum", "avg", "min", "max"])
def test_filtered_aggregate_sort_name_without_from(tmp_path: Path, function: str) -> None:
    sql = f"SELECT {function}(1) FILTER (WHERE true) ORDER BY {function}"
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.attributed and not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["sum(revenue) FILTER (WHERE region = 'north') AS total"])
def test_filtered_aggregate_alias_remains_an_input_read(tmp_path: Path, projected: str) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY sum"
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.region",
        "dm.sales_daily.sum",
    }
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize("function, sort", [('"Sum"', "sum"), ('"Sum"', '"Sum"')])
def test_filtered_aggregate_sort_preserves_function_spelling(
    tmp_path: Path, function: str, sort: str
) -> None:
    sql = (
        f"SELECT {function}(revenue) FILTER (WHERE region = 'north') "
        f"FROM dm.sales_daily ORDER BY {sort}"
    )
    expected = {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    if sort == "sum":
        expected.add("dm.sales_daily.sum")
    assert _reads(sql) == expected
    expected_codes = [] if len(expected) == 2 else ["asset.query_ungoverned_field"]
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == expected_codes


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT +sum(revenue) FILTER (WHERE region = 'north') FROM dm.sales_daily ORDER BY sum",
        "SELECT sum(revenue) FILTER (WHERE region = 'north') "
        "FROM dm.sales_daily ORDER BY dm.sales_daily.sum",
        "SELECT sum(revenue) FILTER (WHERE region = 'north') "
        "FROM dm.sales_daily ORDER BY lower(sum)",
        "SELECT sum(revenue) FILTER (WHERE region = 'north') FROM dm.sales_daily GROUP BY sum",
    ],
)
def test_filtered_aggregate_output_name_boundaries_remain_input_reads(
    tmp_path: Path, sql: str
) -> None:
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.region",
        "dm.sales_daily.sum",
    }
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_filtered_aggregate_ungoverned_predicate_is_read(tmp_path: Path) -> None:
    sql = (
        "SELECT sum(revenue) FILTER (WHERE internal_cost_raw > 0) "
        "FROM dm.sales_daily ORDER BY sum"
    )
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.internal_cost_raw",
    }
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_implicit_sum_names_set_output(tmp_path: Path) -> None:
    sql = (
        "SELECT sum(revenue) FROM dm.sales_daily UNION "
        "SELECT sum(revenue) FROM dm.sales_daily ORDER BY sum"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "function, sort, expected",
    [
        ('"Sum"', '"Sum"', {"dm.sales_daily.revenue"}),
        ('"Sum"', "sum", {"dm.sales_daily.revenue", "dm.sales_daily.sum"}),
        ("sum", '"SUM"', {"dm.sales_daily.revenue", "dm.sales_daily.SUM"}),
    ],
)
def test_implicit_sum_sort_preserves_function_spelling(
    tmp_path: Path, function: str, sort: str, expected: set[str]
) -> None:
    sql = f"SELECT {function}(revenue) FROM dm.sales_daily ORDER BY {sort}"
    assert _reads(sql) == expected
    expected_codes = [] if len(expected) == 1 else ["asset.query_ungoverned_field"]
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == expected_codes


@pytest.mark.parametrize("projected", ["sum(revenue) AS total", "+sum(revenue)", "+(sum(revenue))"])
def test_sum_sort_boundaries_keep_alias_and_unary_plus_rules(
    tmp_path: Path, projected: str
) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY sum"
    assert "dm.sales_daily.revenue" in _reads(sql)
    assert "dm.sales_daily.sum" in _reads(sql)
    assert ungoverned_codes(tmp_path, sql, engine="postgres")


def test_sum_group_by_remains_an_input_read(tmp_path: Path) -> None:
    sql = "SELECT sum(revenue) FROM dm.sales_daily GROUP BY sum"
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.sum"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres")


def test_implicit_count_distinct_on_reads_output(tmp_path: Path) -> None:
    sql = "SELECT DISTINCT ON (count) count(*) FROM dm.sales_daily ORDER BY count"
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.attributed and not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("operator", ["UNION", "INTERSECT", "EXCEPT"])
def test_implicit_count_names_set_output(tmp_path: Path, operator: str) -> None:
    sql = (
        f"SELECT count(*) FROM dm.sales_daily {operator} "
        "SELECT count(*) FROM dm.sales_daily ORDER BY count"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert not refs.attributed and not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["count(*) AS n", "+count(*)", "+(count(*))"])
def test_other_count_projection_does_not_name_set_output(tmp_path: Path, projected: str) -> None:
    sql = (
        f"SELECT {projected} FROM dm.sales_daily UNION "
        "SELECT count(*) FROM dm.sales_daily ORDER BY count"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"count"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_quoted_count_sort_name_must_match_case(tmp_path: Path) -> None:
    sql = 'SELECT count(*) FROM dm.sales_daily ORDER BY "COUNT"'
    assert _reads(sql) == {"dm.sales_daily.COUNT"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize("function", ['"Count"', '"COUNT"'])
def test_quoted_count_function_keeps_its_output_case(tmp_path: Path, function: str) -> None:
    sql = f"SELECT {function}(revenue) FROM dm.sales_daily ORDER BY count"
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.count"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize("function, sort", [('"Count"', '"Count"'), ('"count"', "count")])
def test_matching_quoted_count_function_names_output(
    tmp_path: Path, function: str, sort: str
) -> None:
    sql = f"SELECT {function}(revenue) FROM dm.sales_daily ORDER BY {sort}"
    assert _reads(sql) == {"dm.sales_daily.revenue"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily ORDER BY region",
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY r",
        "SELECT s.r FROM (SELECT region AS r FROM dm.sales_daily INTERSECT "
        "SELECT category FROM dm.sales_daily ORDER BY r) AS s",
        "SELECT s.r FROM (SELECT region AS r FROM dm.sales_daily EXCEPT "
        "SELECT category FROM dm.sales_daily ORDER BY r) AS s",
        "(SELECT region AS r FROM dm.sales_daily) UNION ALL "
        "(SELECT category FROM dm.sales_daily) ORDER BY r LIMIT 5",
    ],
)
def test_set_operation_sort_name_reads_output(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.category"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_quoted_output_alias_does_not_match_unquoted_name(tmp_path: Path) -> None:
    sql = 'SELECT region AS "R" FROM dm.sales_daily ORDER BY r'
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_join_expression_name_remains_ambiguous(tmp_path: Path) -> None:
    sql = (
        "SELECT s.region AS r FROM dm.sales_daily AS s "
        "JOIN dm.stores AS t ON s.store_id = t.id ORDER BY lower(r)"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.region",
        "dm.sales_daily.store_id",
        "dm.stores.id",
    }
    assert {ref.name for ref in refs.ambiguous} == {"r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "r (read from one of dm.sales_daily, dm.stores)" in ungoverned_detail(
        tmp_path / "detail", sql, engine="postgres"
    )


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "SELECT region AS r, rank() OVER (PARTITION BY r ORDER BY r) FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT region AS r, percentile_cont(0.5) WITHIN GROUP (ORDER BY r) "
            "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT string_agg(region, ',' ORDER BY r) AS agg, max(region) AS r "
            "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
        (
            "SELECT array_agg(region ORDER BY r) AS agg, max(region) AS r " "FROM dm.sales_daily",
            {"dm.sales_daily.region", "dm.sales_daily.r"},
        ),
    ],
)
def test_nested_ordering_keeps_input_reads(tmp_path: Path, sql: str, expected: set[str]) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_group_by_keeps_input_first_rule(tmp_path: Path) -> None:
    sql = "SELECT upper(region) AS region, count(*) FROM dm.sales_daily GROUP BY region"
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_clickhouse_sort_expression_keeps_current_reads(tmp_path: Path) -> None:
    sql = "SELECT region AS r FROM dm.sales_daily ORDER BY r + 1"
    assert _reads(sql, dialect="clickhouse") == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="clickhouse") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region FROM dm.sales_daily s ORDER BY s.*",
        "SELECT DISTINCT ON (s.*) region FROM dm.sales_daily s",
    ],
)
def test_qualified_star_keeps_star_remainder(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert refs.starred
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_set_operation_expression_keeps_sourceless_result(tmp_path: Path) -> None:
    sql = (
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY lower(r)"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert refs.unattributed == frozenset({"r"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


@pytest.mark.parametrize("dialect", ["postgres", "clickhouse"])
@pytest.mark.parametrize("operator", ["INTERSECT", "EXCEPT"])
def test_root_intersect_and_except_read_both_branches(dialect: str, operator: str) -> None:
    sql = f"SELECT region FROM dm.sales_daily {operator} SELECT name FROM dm.stores"
    guard_sql(sql, dialect=dialect)
    assert extract_table_names(sql, dialect=dialect) == {"dm.sales_daily", "dm.stores"}
    refs = extract_column_refs(sql, dialect=dialect)
    assert {f"{ref.table.name}.{ref.name}" for ref in refs.attributed} == {
        "dm.sales_daily.region",
        "dm.stores.name",
    }
    assert not refs.ambiguous and not refs.unattributed


@pytest.mark.parametrize("operator", ["INTERSECT", "EXCEPT"])
def test_root_set_operation_sort_name_reads_output(tmp_path: Path, operator: str) -> None:
    sql = (
        f"SELECT region AS r FROM dm.sales_daily {operator} "
        "SELECT category FROM dm.sales_daily ORDER BY r"
    )
    guard_sql(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.category"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("dialect", ["postgres", "clickhouse"])
@pytest.mark.parametrize("operator", ["INTERSECT", "EXCEPT"])
def test_root_set_operation_right_branch_reaches_governance(
    tmp_path: Path, dialect: str, operator: str
) -> None:
    sql = (
        f"SELECT revenue FROM dm.sales_daily {operator} "
        "SELECT internal_cost_raw FROM dm.sales_daily"
    )
    assert ungoverned_pairs(tmp_path, sql, engine=dialect) == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_clickhouse_root_intersect_reads_with_alias_definition(tmp_path: Path) -> None:
    sql = (
        "WITH internal_cost_raw * 2 AS c SELECT c FROM dm.sales_daily "
        "INTERSECT SELECT revenue FROM dm.sales_daily"
    )
    guard_sql(sql, dialect="clickhouse")
    assert _reads(sql, dialect="clickhouse") == {
        "dm.sales_daily.internal_cost_raw",
        "dm.sales_daily.revenue",
    }
    assert ungoverned_pairs(tmp_path, sql, engine="clickhouse") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize("dialect", ["postgres", "clickhouse"])
@pytest.mark.parametrize("operator", ["INTERSECT", "EXCEPT"])
def test_root_set_operation_scans_forbidden_source(dialect: str, operator: str) -> None:
    sql = f"SELECT 1 {operator} SELECT * FROM url('s3://bucket/file.csv')"
    with pytest.raises(SQLGuardError, match="forbidden table function"):
        guard_sql(sql, dialect=dialect)


@pytest.mark.parametrize(
    "suffix", ["", " UNION SELECT 1", " INTERSECT SELECT 1", " EXCEPT SELECT 1"]
)
def test_select_into_is_rejected(suffix: str) -> None:
    sql = f"SELECT 1 INTO TEMP review_probe_table{suffix}"
    with pytest.raises(SQLGuardError, match="forbidden construct in SQL: Into"):
        guard_sql(sql, dialect="postgres")


@pytest.mark.parametrize("operator", ["UNION", "INTERSECT", "EXCEPT"])
@pytest.mark.parametrize("modifier", ["LIMIT", "OFFSET"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_set_operation_scalar_modifier_reads_its_subquery(
    tmp_path: Path, operator: str, modifier: str, wrapped: bool
) -> None:
    body = f"SELECT revenue FROM dm.sales_daily {operator} SELECT revenue FROM dm.sales_daily"
    sql = (
        f"({body})" if wrapped else body
    ) + f" {modifier} (SELECT max(s.internal_cost_raw)::int FROM dm.sales_daily AS s)"
    guard_sql(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.internal_cost_raw",
    }
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +r", "dm.sales_daily.r"),
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +(r)", "dm.sales_daily.r"),
        ("SELECT revenue AS R FROM dm.sales_daily ORDER BY +(+r)", "dm.sales_daily.r"),
        ("SELECT DISTINCT ON (+r) region AS r FROM dm.sales_daily", "dm.sales_daily.r"),
        ('SELECT region AS "R" FROM dm.sales_daily ORDER BY +"R"', "dm.sales_daily.R"),
        ("SELECT upper(region) AS m FROM dm.sales_daily GROUP BY +m", "dm.sales_daily.m"),
    ],
)
def test_unary_plus_name_is_input(tmp_path: Path, sql: str, expected: str) -> None:
    assert expected in _reads(sql)
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_unary_plus_set_sort_stays_sourceless(tmp_path: Path) -> None:
    sql = (
        "SELECT region AS r FROM dm.sales_daily UNION ALL "
        "SELECT category FROM dm.sales_daily ORDER BY +r"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"r"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)) x",
        "WITH c AS ((SELECT region AS r FROM dm.sales_daily) ORDER BY lower(r)) " "SELECT r FROM c",
        "SELECT x.r FROM ((SELECT s.region AS r FROM dm.sales_daily s) " "ORDER BY lower(s.r)) x",
    ],
)
def test_parenthesized_select_expression_sort_reads_input(tmp_path: Path, sql: str) -> None:
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.r"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT x.r FROM ((SELECT region AS R FROM dm.sales_daily) ORDER BY r) x",
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily) ORDER BY r) x",
    ],
)
def test_parenthesized_output_sort_is_clean(tmp_path: Path, sql: str) -> None:
    assert "r" not in extract_column_refs(sql, dialect="postgres").unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_parenthesized_query_with_inner_order_keeps_existing_reads(tmp_path: Path) -> None:
    sql = (
        "SELECT x.r FROM ((SELECT region AS r FROM dm.sales_daily ORDER BY region) "
        "ORDER BY lower(r)) x"
    )
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["(region)", "((region))"])
def test_parenthesized_leftmost_set_output_name_is_clean(tmp_path: Path, projected: str) -> None:
    sql = (
        f"SELECT {projected} FROM dm.sales_daily UNION "
        "SELECT category FROM dm.sales_daily ORDER BY region"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_parenthesized_plain_output_wins_join_ambiguity(tmp_path: Path) -> None:
    sql = "SELECT (s.id) FROM dm.stores s " "JOIN dm.stores t ON s.id = t.id ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").ambiguous == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_unary_plus_projection_does_not_name_set_output(tmp_path: Path) -> None:
    sql = (
        "SELECT +revenue FROM dm.sales_daily UNION "
        "SELECT revenue FROM dm.sales_daily ORDER BY revenue"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"revenue"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_unary_plus_projection_does_not_hide_join_ambiguity(tmp_path: Path) -> None:
    sql = "SELECT +(s.id) FROM dm.stores s " "JOIN dm.stores t ON s.id = t.id ORDER BY id"
    assert {ref.name for ref in extract_column_refs(sql, dialect="postgres").ambiguous} == {"id"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_explicit_alias_on_unary_plus_projection_names_output(tmp_path: Path) -> None:
    sql = (
        "SELECT +revenue AS revenue FROM dm.sales_daily UNION "
        "SELECT revenue FROM dm.sales_daily ORDER BY revenue"
    )
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("projected", ["+dm.stores.id", "+(dm.stores.id)"])
def test_unary_plus_three_part_projection_does_not_name_set_output(
    tmp_path: Path, projected: str
) -> None:
    sql = f"SELECT {projected} FROM dm.stores UNION SELECT id FROM dm.stores ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset({"id"})
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_fields_unchecked", "advisory")
    ]


def test_explicit_alias_on_unary_plus_three_part_projection_names_output(tmp_path: Path) -> None:
    sql = "SELECT +dm.stores.id AS id FROM dm.stores UNION SELECT id FROM dm.stores ORDER BY id"
    assert extract_column_refs(sql, dialect="postgres").unattributed == frozenset()
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []
