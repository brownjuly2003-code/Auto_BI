"""Exact SQL forms for changed identifiers and PostgreSQL escape literals."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from auto_bi.agent.sql_guard import SQLGuardError
from tests.test_generated_sql_escaping import signature, site_sql, superset_sql

PG_SITES = ["flat", "window", "compare", "histogram", "dataset", "scaled"]
PG_QUOTE_CASES = [
    (r"a\'b", r"E'a\\''b'"),
    ("'\\", r"E'''\\'"),
    ("\\'", r"E'\\'''"),
]
CHANGED = json.loads(
    (Path(__file__).parent / "fixtures/generated_sql_changed.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("site", PG_SITES)
@pytest.mark.parametrize("op", ["=", "!=", ">=", "<=", "in"])
@pytest.mark.parametrize(("value", "literal"), PG_QUOTE_CASES)
def test_postgres_quote_bytes(site: str, op: str, value: str, literal: str) -> None:
    actual = site_sql(site, "marker_name", value, "postgres", op)
    baseline = site_sql(site, "marker_name", "marker_value", "postgres", op)
    assert literal in actual
    assert actual.replace(literal, "'marker_value'") == baseline
    assert re.search(r"(?<!\\)(?:\\\\)*\\'", literal) is None


@pytest.mark.parametrize(("value", "literal"), PG_QUOTE_CASES)
def test_m20_text_differs_despite_same_sqlglot_value(value: str, literal: str) -> None:
    body = value.replace("\\", "\\\\").replace("'", "\\'")
    mutant = f"E'{body}'"
    assert mutant != literal
    for text in (mutant, literal):
        parsed = sqlglot.parse_one("SELECT " + text, read="postgres")
        string = parsed.find(exp.ByteString)
        assert string is not None
        assert string.this == value


@pytest.mark.parametrize("case", CHANGED, ids=[case["id"] for case in CHANGED])
def test_changed_name_bytes_and_head_reading(case: dict[str, str]) -> None:
    name, site = case["name"], case["site"]
    actual = (
        superset_sql(site, name)
        if case["kind"] == "superset"
        else site_sql(site, name, "marker_value", "clickhouse")
    )
    baseline = (
        superset_sql(site, "marker_name")
        if case["kind"] == "superset"
        else site_sql(site, "marker_name", "marker_value", "clickhouse")
    )
    assert actual.encode("utf-8") == case["sql"].encode("utf-8")
    old = case["head_sql"]
    assert hashlib.sha256(old.encode("utf-8")).hexdigest() == case["head_sha256"]
    assert hashlib.sha256(actual.encode("utf-8")).hexdigest() != case["head_sha256"]
    expected = signature(baseline, "clickhouse", "marker_name", "marker_value")
    assert signature(actual, "clickhouse", name, "marker_value") == expected
    try:
        previous = signature(old, "clickhouse", name, "marker_value")
    except (AssertionError, ValueError, ParseError, TokenError, SQLGuardError):
        assert not case["id"].startswith(("1/", "3/"))
        return
    assert (previous == expected) == case["id"].startswith(("1/", "3/"))
