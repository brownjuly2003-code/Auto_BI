"""E1 parser refusals at the guard: a third-party parser failure becomes SQLGuardError text
with the error class, the call and at most a position - no SQL, token or parser message.

Apart from test_entry_sql.py so that mutmut's selection holds these guard-level tests
without the verify-level ones (docs/operations/SLO.md, Mutation smoke).
"""

from __future__ import annotations

import pytest
import sqlglot

from auto_bi.agent import sql_guard

SECRET = "private-sql-marker"
BAD_SQL = [
    pytest.param("SELECT '" + SECRET, "TokenError", id="unclosed-quote"),
    pytest.param(
        "SELECT \"region\" ('North') FROM dm.sales_daily", "AttributeError", id="missing-in"
    ),
    pytest.param("SELECT FROM", "ParseError", id="parse-error"),
]


@pytest.mark.parametrize("sql, error_class", BAD_SQL)
@pytest.mark.parametrize(
    "entry", [sql_guard.guard_sql, sql_guard.extract_table_refs, sql_guard.extract_column_refs]
)
def test_real_parser_errors_are_safe_guard_refusals(sql, error_class, entry):
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        entry(sql)
    assert type(caught.value.__cause__).__name__ == error_class
    assert error_class in str(caught.value)
    assert "sqlglot.parse" in str(caught.value)
    assert sql not in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("error_class", [ValueError, IndexError, AttributeError])
def test_other_parser_failures_are_not_silently_ignored(monkeypatch, error_class):
    def fail(*args, **kwargs):
        raise error_class(SECRET)

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard.guard_sql("SELECT 1")
    assert str(caught.value) == (
        f"SQL does not parse: {error_class.__name__} at sqlglot.parse; position unavailable"
    )
    assert isinstance(caught.value.__cause__, error_class)


def test_our_own_post_parse_error_propagates(monkeypatch):
    def fail(root):
        raise AttributeError("our normalization bug")

    monkeypatch.setattr(sql_guard, "_normalize_postgres_root_subquery", fail)
    with pytest.raises(AttributeError, match="our normalization bug"):
        sql_guard.guard_sql("(SELECT 1)", dialect="postgres")


@pytest.mark.parametrize("dialect", ["clickhouse", "postgres"])
@pytest.mark.parametrize(
    "error_class",
    [sqlglot.errors.TokenError, AttributeError, ValueError, IndexError, RecursionError],
)
@pytest.mark.parametrize(
    "entry", [sql_guard.guard_sql, sql_guard.extract_table_refs, sql_guard.extract_column_refs]
)
def test_parser_failure_boundary_on_both_dialects(monkeypatch, dialect, error_class, entry):
    failure = error_class(SECRET)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        entry("SELECT 1", dialect=dialect)
    assert caught.value.__cause__ is failure
    assert error_class.__name__ in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("error_class", [KeyboardInterrupt, SystemExit])
def test_process_control_is_not_a_guard_finding(monkeypatch, error_class):
    def fail(*args, **kwargs):
        raise error_class

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(error_class):
        sql_guard.guard_sql("SELECT 1")


def test_parse_error_coordinates_are_kept_without_tokens():
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard.guard_sql("SELECT FROM")
    assert str(caught.value) == "SQL does not parse: ParseError at sqlglot.parse; line 1, column 11"


def test_postgres_secondary_tokenizer_is_a_narrow_guard_boundary(monkeypatch):
    failure = ValueError(SECRET)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sqlglot.Dialect.get_or_raise("postgres").__class__, "tokenize", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard._postgres_sort_token_info("SELECT 1", "postgres")
    assert caught.value.__cause__ is failure
    assert str(caught.value) == (
        "SQL does not parse: ValueError at sqlglot.tokenize; position unavailable"
    )


@pytest.mark.parametrize("line, column", [(1, None), (None, 11), ("1", 11)])
def test_parse_error_without_two_integer_coordinates_names_no_position(monkeypatch, line, column):
    failure = sqlglot.errors.ParseError(SECRET, errors=[{"line": line, "col": column}])

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(sqlglot, "parse", fail)
    with pytest.raises(sql_guard.SQLGuardError) as caught:
        sql_guard.guard_sql("SELECT 1")
    assert str(caught.value) == (
        "SQL does not parse: ParseError at sqlglot.parse; position unavailable"
    )
