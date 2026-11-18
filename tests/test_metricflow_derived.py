"""What MetricFlow does with a derived quotient (ADR 0012, D4b), held to the resolver.

`tests/fixtures/dbt_semantic_manifest/derived/` keeps the record of
`scripts/metricflow_derived.py` on PostgreSQL 16.15: the artifact dbt-core 1.12.6 wrote for the
script's project, and per metric MetricFlow 0.213.0's SQL and what PostgreSQL gave for it over
the parity recipe's rows — its rows with the type of the value, or the SQLSTATE of the error.
The project holds, for each pair of the script's `PAIRS`, a derived quotient of two metrics and
the ratio metric over them. Nothing here runs dbt, MetricFlow or a database: the values are held
to the recipe's rows, computed here exactly.

The record is why the resolver answers a derived quotient with its own construct gap (row
`derived.quotient` of `docs/specs/dbt-formula-inventory.md`) and not with the typed ratio, the
quotient of a ratio metric: MetricFlow writes the derived one as written, so on PostgreSQL a
non-NULL numerator over a zero denominator fails the query and two integer terms divide with
truncation.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.semantic.metricflow import read_semantic_manifest
from auto_bi.semantic.metricflow_formula import (
    _DERIVED_QUOTIENT,
    CONSTRUCT_UNSUPPORTED,
    PROVEN_PARITY,
    resolve_formula,
)
from tests.test_metricflow_formula import FIXTURES, REPO

RECORD = FIXTURES / "derived"
#: PostgreSQL's SQLSTATE `division_by_zero`.
DIVISION_BY_ZERO = "22012"
#: How far a double may be from the exact quotient: a relative error, ten times a double's
#: machine epsilon.
DOUBLE_ERROR = Fraction(22, 10**16)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "metricflow_derived", REPO / "scripts" / "metricflow_derived.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _script()
PARITY = SCRIPT.parity
RESULT: dict[str, Any] = json.loads((RECORD / "result.json").read_bytes())
RAW = (RECORD / "semantic_manifest.json").read_bytes()
ARTIFACT: dict[str, Any] = json.loads(RAW)
Key = tuple[str, int]


def _metric(name: str) -> dict[str, Any]:
    return next(metric for metric in ARTIFACT["metrics"] if metric["name"] == name)


def _outcome(name: str) -> dict[str, Any]:
    outcome: dict[str, Any] = RESULT["metrics"][name]["outcome"]
    return outcome


def _rows(name: str) -> dict[Key, tuple[Fraction | None, str]]:
    """A metric's recorded rows: key -> (value, PostgreSQL type)."""
    return {
        (day, store): (None if value is None else Fraction(value), kind)
        for day, store, value, kind in _outcome(name)["rows"]
    }


def _groups() -> dict[Key, dict[str, list[Fraction]]]:
    """The recipe's rows by (day, store): the values of each column, NULL left out."""
    groups: dict[Key, dict[str, list[Fraction]]] = defaultdict(lambda: defaultdict(list))
    for day, store, *values in PARITY.table_rows():
        for column, value in zip(PARITY.COLUMNS, values, strict=True):
            if value is not None:
                groups[(day, store)][column].append(Fraction(str(value)))
    return groups


def _term(group: dict[str, list[Fraction]], metric: str) -> Fraction | None:
    """A simple metric of the script over a group, as SQL aggregates: NULL over no value, but
    a count, which is 0."""
    column, agg = SCRIPT.SIMPLE[metric]
    values = group[column]
    if agg == "count":
        return Fraction(len(values))
    if not values:
        return None
    return sum(values, Fraction(0)) if agg == "sum" else max(values)


def _close(found: Fraction | None, exact: Fraction | None) -> bool:
    if found is None or exact is None:
        return found is exact
    return abs(found - exact) <= DOUBLE_ERROR * abs(exact)


def _truncated(top: Fraction, bottom: Fraction) -> Fraction:
    """PostgreSQL's integer division: the quotient truncated toward zero."""
    return Fraction(int(top / bottom))


def test_the_record_is_of_this_recipe_and_these_versions() -> None:
    assert RESULT["recipe"] == SCRIPT.recipe()
    assert RESULT["artifact_digest"] == digest_bytes(RAW)
    versions = RESULT["versions"]
    assert (versions["metricflow"], versions["dbt-core"]) == ("0.213.0", "1.12.6")
    assert versions["postgres"].startswith("PostgreSQL 16.15 ")


def test_the_artifact_holds_the_projects_metrics_as_written() -> None:
    written = {metric["name"]: metric for metric in SCRIPT._metrics()}
    assert sorted(metric["name"] for metric in ARTIFACT["metrics"]) == sorted(written)
    for name, metric in written.items():
        found = _metric(name)
        assert found["type"] == metric["type"], name
        params = metric["type_params"]
        if metric["type"] == "derived":
            assert found["type_params"]["expr"] == params["expr"]
            assert [(item["name"], item["alias"]) for item in found["type_params"]["metrics"]] == [
                (item["name"], item.get("alias")) for item in params["metrics"]
            ]
        if metric["type"] == "ratio":
            assert found["type_params"]["numerator"]["name"] == params["numerator"]
            assert found["type_params"]["denominator"]["name"] == params["denominator"]
    assert set(RESULT["metrics"]) == {
        name for name, metric in written.items() if metric["type"] != "simple"
    }


@pytest.mark.parametrize("pair", SCRIPT.PAIRS, ids=lambda pair: SCRIPT.derived_name(*pair))
def test_metricflow_writes_the_derived_quotient_as_written_and_the_ratio_with_nullif(
    pair: tuple[str, str, tuple[str, str] | None],
) -> None:
    top, bottom, aliases = pair
    name = SCRIPT.derived_name(top, bottom, aliases)
    columns = aliases or (top, bottom)
    derived = RESULT["metrics"][name]["sql"]
    assert f"\n  , {columns[0]} / {columns[1]} AS {name}\n" in derived
    assert "NULLIF" not in derived and "DOUBLE PRECISION" not in derived
    ratio = SCRIPT.ratio_name(top, bottom)
    assert (
        f"\n  , CAST({top} AS DOUBLE PRECISION) / CAST(NULLIF({bottom}, 0) AS DOUBLE PRECISION)"
        f" AS {ratio}\n"
    ) in RESULT["metrics"][ratio]["sql"]


def test_a_zero_denominator_fails_the_derived_query_where_the_ratio_gives_null() -> None:
    groups = _groups()
    zero = {key for key, group in groups.items() if _term(group, "qty_count") == 0}
    # a group whose count is 0 and whose sum is not NULL: NULL / 0 is NULL on PostgreSQL
    assert any(_term(groups[key], "amount_sum") is not None for key in zero)
    assert _outcome("d_amount_sum_by_qty_count") == {"error": DIVISION_BY_ZERO}
    ratio = _rows("r_amount_sum_by_qty_count")
    assert set(ratio) == set(groups)
    for key, group in groups.items():
        top, bottom = _term(group, "amount_sum"), _term(group, "qty_count")
        exact = None if top is None or not bottom else top / bottom
        assert ratio[key][1] == "double precision"
        assert _close(ratio[key][0], exact), key
    assert all(ratio[key][0] is None for key in zero)


def test_two_integer_terms_divide_with_truncation_where_the_ratio_does_not() -> None:
    groups = _groups()
    derived = _rows("d_qty_nn_max_by_qty_nn_count")
    ratio = _rows("r_qty_nn_max_by_qty_nn_count")
    assert set(derived) == set(ratio) == set(groups)
    truncated = set()
    for key, group in groups.items():
        top, bottom = _term(group, "qty_nn_max"), _term(group, "qty_nn_count")
        assert top is not None and bottom, key
        exact = top / bottom
        assert derived[key] == (_truncated(top, bottom), "bigint"), key
        assert ratio[key][1] == "double precision"
        assert _close(ratio[key][0], exact), key
        if _truncated(top, bottom) != exact:
            truncated.add(key)
    # 2 / 4 at (2026-01-01, 1) is 0, and the ratio's 0.5
    assert ("2026-01-01", 1) in truncated
    assert derived[("2026-01-01", 1)][0] == 0 and ratio[("2026-01-01", 1)][0] == Fraction(1, 2)


@pytest.mark.parametrize("aliases", [None, ("top_value", "bottom_value")])
def test_a_numeric_quotient_without_a_zero_agrees_with_its_ratio(
    aliases: tuple[str, str] | None,
) -> None:
    groups = _groups()
    derived = _rows(SCRIPT.derived_name("amount_sum", "qty_nn_count", aliases))
    ratio = _rows(SCRIPT.ratio_name("amount_sum", "qty_nn_count"))
    assert set(derived) == set(ratio) == set(groups)
    for key, group in groups.items():
        top, bottom = _term(group, "amount_sum"), _term(group, "qty_nn_count")
        assert bottom, key
        exact = None if top is None else top / bottom
        assert derived[key][1] == "numeric"
        assert _close(derived[key][0], exact), key
        assert _close(ratio[key][0], exact), key


def _resolution(name: str) -> Any:
    read = read_semantic_manifest(RAW)
    assert read.manifest is not None, read.problems
    contract = MetricContract.model_validate(
        {
            "metadata": {
                "id": f"metric://derived/{name}",
                "version": 1,
                "owner": "commercial-analytics",
                "status": "active",
            },
            "formula": {
                "kind": "external_ref",
                "external": {
                    "source": "dbt_semantic_manifest",
                    "ref": name,
                    "source_revision": read.manifest.closure_digest(name),
                },
            },
            "grain": {"table": f"{PARITY.SCHEMA}.{PARITY.TABLE}", "keys": list(PARITY.KEYS)},
            "additivity": "non_additive",
            "time": {"event_time_column": PARITY.EVENT_TIME, "timezone": "UTC"},
            "unit": {
                "unit": "value",
                "precision": 2,
                "null_semantics": "null_is_missing",
                "zero_semantics": "zero_is_value",
            },
            "freshness": {"max_age": "PT6H"},
            "classification": "internal",
        }
    )
    return resolve_formula(
        contract,
        read,
        database=PARITY.DATABASE,
        declared=PARITY.DECLARED["postgres.date"],
        proven=PROVEN_PARITY,
    )


@pytest.mark.parametrize("pair", SCRIPT.PAIRS, ids=lambda pair: SCRIPT.derived_name(*pair))
def test_the_resolver_answers_the_quotient_at_its_expr_and_types_the_ratio(
    pair: tuple[str, str, tuple[str, str] | None],
) -> None:
    top, bottom, aliases = pair
    name = SCRIPT.derived_name(top, bottom, aliases)
    derived = _resolution(name)
    position = [metric["name"] for metric in ARTIFACT["metrics"]].index(name)
    assert derived.formula is None
    assert [(gap.code, gap.severity.value, gap.path) for gap in derived.gaps] == [
        (CONSTRUCT_UNSUPPORTED, "review", f"$.metrics[{position}].type_params.expr")
    ]
    assert re.search(f": {re.escape(_DERIVED_QUOTIENT)}$", derived.gaps[0].reason)
    # the same two metrics as a ratio metric: a typed ratio, on rows the parity run proved
    ratio = _resolution(SCRIPT.ratio_name(top, bottom))
    assert ratio.gaps == ()
    assert ratio.formula is not None and ratio.formula.kind == "typed_ratio"
    assert ratio.parity_rows <= PROVEN_PARITY


def test_the_script_loads_the_parity_script_from_beside_it() -> None:
    assert Path(PARITY.__file__) == REPO / "scripts" / "metricflow_parity.py"
    assert SCRIPT.parity.BRIDGE == REPO / "scripts" / "metricflow_parity_sql.py"
