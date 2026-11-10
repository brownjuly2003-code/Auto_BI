"""PostgreSQL resolves strong output names through CAST expressions."""

from pathlib import Path

import pytest

from auto_bi.agent.sql_guard import extract_column_refs
from tests.test_sql_guard_clickhouse_alias import (
    ungoverned_codes,
    ungoverned_detail,
    ungoverned_pairs,
)


def _reads(sql: str, *, dialect: str = "postgres") -> set[str]:
    refs = extract_column_refs(sql, dialect=dialect)
    return {f"{ref.table.name}.{ref.name}" for ref in refs.attributed}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(count(*) AS bigint) FROM dm.sales_daily ORDER BY count",
        "SELECT count(*)::bigint FROM dm.sales_daily ORDER BY count",
        "SELECT CAST((count(*)::bigint) AS numeric) FROM dm.sales_daily ORDER BY count",
    ],
)
def test_cast_count_sort_name_reads_cast_output(tmp_path: Path, sql: str) -> None:
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == set()
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_plain_column_sort_name_reads_cast_output(tmp_path: Path) -> None:
    sql = "SELECT CAST(region AS text) FROM dm.sales_daily ORDER BY region"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("function", ["sum", "avg", "min", "max"])
def test_cast_aggregate_sort_names_read_output_and_arguments(tmp_path: Path, function: str) -> None:
    argument = "*" if function == "count" else "revenue"
    sql = f"SELECT CAST({function}({argument}) AS numeric) FROM dm.sales_daily ORDER BY {function}"
    refs = extract_column_refs(sql, dialect="postgres")
    expected = set() if function == "count" else {"dm.sales_daily.revenue"}
    assert _reads(sql) == expected
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_filtered_aggregate_sort_name_reads_filter_and_argument(tmp_path: Path) -> None:
    sql = (
        "SELECT CAST(sum(revenue) FILTER (WHERE region = 'north') AS numeric) "
        "FROM dm.sales_daily ORDER BY sum"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.revenue", "dm.sales_daily.region"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_windowed_aggregate_sort_name_reads_window_inputs(tmp_path: Path) -> None:
    sql = (
        "SELECT CAST(avg(revenue) OVER (PARTITION BY region ORDER BY date) AS numeric) "
        "FROM dm.sales_daily ORDER BY avg"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {
        "dm.sales_daily.revenue",
        "dm.sales_daily.region",
        "dm.sales_daily.date",
    }
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_windowed_row_number_sort_name_reads_window_inputs(tmp_path: Path) -> None:
    sql = (
        "SELECT CAST(row_number() OVER (PARTITION BY region ORDER BY date) AS bigint) "
        "FROM dm.sales_daily ORDER BY row_number"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.date"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_count_distinct_on_uses_cast_output_name(tmp_path: Path) -> None:
    sql = (
        "SELECT DISTINCT ON (count) CAST(count(*) AS bigint) " "FROM dm.sales_daily ORDER BY count"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == set()
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize("operator", ["UNION", "INTERSECT", "EXCEPT"])
def test_cast_count_set_operation_sort_uses_left_branch_output(
    tmp_path: Path, operator: str
) -> None:
    sql = (
        f"SELECT CAST(count(*) AS bigint) FROM dm.sales_daily {operator} "
        "SELECT CAST(1 AS bigint) ORDER BY count"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == set()
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_sourceless_cast_count_sort_name_is_still_an_output(tmp_path: Path) -> None:
    sql = "SELECT CAST(count(*) AS bigint) ORDER BY count"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == set()
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_count_group_by_name_stays_an_input(tmp_path: Path) -> None:
    sql = "SELECT CAST(count(*) AS bigint) FROM dm.sales_daily GROUP BY count"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.count"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


def test_cast_qualified_column_wins_self_join_output_name_without_ambiguity(
    tmp_path: Path,
) -> None:
    sql = (
        "SELECT CAST(s.region AS text) FROM dm.sales_daily AS s "
        "JOIN dm.sales_daily AS t ON s.store_id = t.store_id ORDER BY region"
    )
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == {"dm.sales_daily.region", "dm.sales_daily.store_id"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


def test_cast_alias_replaces_implicit_output_name(tmp_path: Path) -> None:
    sql = "SELECT CAST(count(*) AS bigint) AS total FROM dm.sales_daily ORDER BY count"
    assert _reads(sql) == {"dm.sales_daily.count"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "dm.sales_daily.count" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


def test_cast_quoted_output_name_is_respected(tmp_path: Path) -> None:
    sql = 'SELECT CAST(count(*) AS bigint) AS "Count" FROM dm.sales_daily ORDER BY "Count"'
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == set()
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == []


@pytest.mark.parametrize(
    "sort, expected",
    [
        ('"Sum"', {"dm.sales_daily.revenue"}),
        ("sum", {"dm.sales_daily.revenue", "dm.sales_daily.sum"}),
    ],
)
def test_cast_quoted_function_name_controls_output_spelling(
    tmp_path: Path, sort: str, expected: set[str]
) -> None:
    sql = f'SELECT CAST("Sum"(revenue) AS numeric) FROM dm.sales_daily ORDER BY {sort}'
    assert _reads(sql) == expected
    expected_codes = [] if len(expected) == 1 else ["asset.query_ungoverned_field"]
    assert ungoverned_codes(tmp_path, sql, engine="postgres") == expected_codes


@pytest.mark.parametrize(
    "projected",
    [
        "CAST(+count(*) AS bigint)",
        "+CAST(count(*) AS bigint)",
        "CAST(+CAST(count(*) AS bigint) AS numeric)",
        "+count(*)::bigint",
        "+(CAST((count(*)) AS bigint))",
    ],
)
def test_unary_plus_prevents_cast_from_naming_output(tmp_path: Path, projected: str) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY count"
    assert "dm.sales_daily.count" in _reads(sql)
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize("sort", ["dm.sales_daily.count", "lower(count)"])
def test_qualified_or_expression_sort_stays_an_input(tmp_path: Path, sort: str) -> None:
    sql = f"SELECT CAST(count(*) AS bigint) FROM dm.sales_daily ORDER BY {sort}"
    assert _reads(sql) == {"dm.sales_daily.count"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert "dm.sales_daily.count" in ungoverned_detail(tmp_path / "detail", sql, engine="postgres")


def test_cast_sum_keeps_unknown_argument_without_phantom_output(tmp_path: Path) -> None:
    sql = "SELECT CAST(sum(internal_cost_raw) AS numeric) FROM dm.sales_daily ORDER BY sum"
    assert _reads(sql) == {"dm.sales_daily.internal_cost_raw"}
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    detail = ungoverned_detail(tmp_path / "detail", sql, engine="postgres")
    assert "dm.sales_daily.internal_cost_raw" in detail
    assert "dm.sales_daily.sum" not in detail


@pytest.mark.parametrize(
    "sql, expected",
    [
        (
            "SELECT CAST(1 AS bigint) FROM dm.sales_daily ORDER BY bigint",
            {"dm.sales_daily.bigint"},
        ),
        (
            "SELECT CAST(revenue + 1 AS bigint) FROM dm.sales_daily ORDER BY bigint",
            {"dm.sales_daily.revenue", "dm.sales_daily.bigint"},
        ),
        (
            "SELECT CAST(array_agg(revenue) AS text) FROM dm.sales_daily ORDER BY array_agg",
            {"dm.sales_daily.revenue", "dm.sales_daily.array_agg"},
        ),
    ],
)
def test_cast_does_not_infer_type_operator_or_new_function_names(
    tmp_path: Path, sql: str, expected: set[str]
) -> None:
    assert _reads(sql) == expected
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]


@pytest.mark.parametrize(
    "projected, sort, expected",
    [
        (
            "date_to_date_str(row_number() OVER ())",
            "row_number",
            {"dm.sales_daily.row_number"},
        ),
        (
            "time_to_time_str(sum(revenue) OVER ())",
            "sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
        (
            "CAST(date_to_date_str(row_number() OVER ()) AS text)",
            "row_number",
            {"dm.sales_daily.row_number"},
        ),
        (
            "time_to_time_str(sum(revenue) OVER ())::text",
            "sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
        (
            '"date_to_date_str"(row_number() OVER ())',
            "row_number",
            {"dm.sales_daily.row_number"},
        ),
        (
            '"time_to_time_str"(sum(revenue) OVER ())',
            "sum",
            {"dm.sales_daily.revenue", "dm.sales_daily.sum"},
        ),
    ],
)
def test_function_normalized_to_cast_keeps_sort_input_read(
    tmp_path: Path, projected: str, sort: str, expected: set[str]
) -> None:
    sql = f"SELECT {projected} FROM dm.sales_daily ORDER BY {sort}"
    refs = extract_column_refs(sql, dialect="postgres")
    assert _reads(sql) == expected
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_pairs(tmp_path, sql, engine="postgres") == [
        ("asset.query_ungoverned_field", "review")
    ]
    assert f"dm.sales_daily.{sort}" in ungoverned_detail(
        tmp_path / "detail", sql, engine="postgres"
    )


def test_clickhouse_cast_count_sort_name_remains_an_input(tmp_path: Path) -> None:
    sql = "SELECT CAST(count(*) AS bigint) FROM dm.sales_daily ORDER BY count"
    refs = extract_column_refs(sql, dialect="clickhouse")
    assert _reads(sql, dialect="clickhouse") == {"dm.sales_daily.count"}
    assert not refs.ambiguous and not refs.unattributed
    assert ungoverned_pairs(tmp_path, sql, engine="clickhouse") == [
        ("asset.query_ungoverned_field", "review")
    ]
