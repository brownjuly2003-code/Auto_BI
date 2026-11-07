"""The corpus of ADR 0008, slice S5b: what dbt-core 1.12.6 writes for each row of the inventory.

`scripts/dbt_corpus.py run` parsed, on the Mac stand, one dbt project per entry — one construct
as an author writes it, in the legacy dialect or in V2 — and then the entries dbt accepted
together, once per dialect. `tests/fixtures/dbt_semantic_manifest/corpus` keeps both artifacts
and `result.json`: the versions, the recipe, the digest of each artifact, and for each entry
dbt's error or what the resolver answered. These tests hold the record to the script, resolve
each accepted entry again on its artifact, hold dbt's reason for each entry it refused and why
each entry it accepted misses its row where it does, and hold the section "What dbt-core 1.12.6
writes" of `docs/specs/dbt-formula-inventory.md` to the record.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

import pytest

from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.metricflow import ManifestRead, read_semantic_manifest
from auto_bi.semantic.metricflow_formula import OUTSIDE_GRAIN, PROVEN_PARITY
from tests.test_dbt_formula_inventory import ROWS, SIDES, SPEC, _shape
from tests.test_metricflow_formula import FIXTURES, REPO

CORPUS = FIXTURES / "corpus"
CLASSES = ("Written", "Refused", "Not carried", "No entry")
SECTION = "## What dbt-core 1.12.6 writes"
UNNESTED = {"require_nested_cumulative_type_params": False}
#: The resolver's reason for a construct outside the subset, before the construct.
CONSTRUCT = "outside the subset a typed formula is resolved from: "
#: The resolver's reason for a parity gap, before the rows of the matrix it names.
PARITY = "no parity run has shown MetricFlow and the reference SQL to agree on "
#: The classes of a column's type the parity run took.
RUN_CLASSES = ("decimal", "integer", "float")
#: The engines of the contexts the parity run proved rows in.
RUN_ENGINES = frozenset(row.split(".", 1)[0] for row in PROVEN_PARITY)

#: Each entry dbt refuses, and what it says. The JSON schema of dbt's properties refuses a key
#: it does not know; dbt's own checks refuse the rest.
REFUSALS = {
    "c_metric_datatype": "Additional properties are not allowed ('datatype' was unexpected)",
    "c_private": "Additional properties are not allowed ('hidden' was unexpected)",
    "c_params_join_to_timespine": "path ['type_params']: Additional properties are not allowed"
    " ('join_to_timespine' was unexpected)",
    "c_params_fill_nulls_with": "path ['type_params']: Additional properties are not allowed"
    " ('fill_nulls_with' was unexpected)",
    "c_params_metric_aggregation_params": "path ['type_params']: Additional properties are not"
    " allowed ('metric_aggregation_params' was unexpected)",
    "c_simple_both": "path ['type_params']: Additional properties are not allowed"
    " ('metric_aggregation_params' was unexpected)",
    "c_legacy_input_measures": "path ['type_params']: Additional properties are not allowed"
    " ('input_measures' was unexpected)",
    "c_ratio_input_measures": "path ['type_params']: Additional properties are not allowed"
    " ('input_measures' was unexpected)",
    "c_v2_metric_datatype": "'datatype': 'decimal'}] is not valid under any of the given schemas",
    "c_v2_input_measures": "'input_measures': [{'name': 'revenue'}]}] is not valid under any of"
    " the given schemas",
    "c_v2_params_metric_aggregation_params": "simple metrics in v2 YAML must be attached to"
    " semantic_model",
    "c_params_window": "should be nested under `type_params.cumulative_type_params.window`",
    "c_params_grain_to_date": "should be nested under `type_params.cumulative_type_params.window`",
    "c_simple_neither": "should have a measure or agg type defined, but it does not",
    "c_v2_simple_neither": "should have a measure or agg type defined, but it does not",
    "c_legacy_agg_params_percentile": "with aggregation 'sum' uses agg_params (percentile) only"
    " relevant to Percentile measures",
    "c_legacy_sum_use_discrete_percentile": "with aggregation 'sum' uses agg_params"
    " (use_discrete_percentile) only relevant to Percentile measures",
    "c_legacy_sum_use_approximate_percentile": "with aggregation 'sum' uses agg_params"
    " (use_approximate_percentile) only relevant to Percentile measures",
    "c_legacy_measure_name_0": "'null' is an SQL reserved keyword, and thus cannot be used as a"
    " measure 'name'",
    "c_time_name_0": "'null' is an SQL reserved keyword, and thus cannot be used as a dimension"
    " 'name'",
    "c_legacy_measure_name_1": "Invalid name `Gross_Margin` - names may only contain lower case",
    "c_time_name_1": "Invalid name `Date` - names may only contain lower case",
    "c_time_none_legacy": "AssertionError: Aggregation time dimension for measure",
    "c_time_not_time": "has the aggregation time dimension set to 'kind', which is not a valid"
    " time dimension",
    "c_v2_time_not_time": "Default aggregation time dimension was specified as 'region' which"
    " doesn't exist as a time dimension",
    "c_ratio_numerator_missing": "Both a numerator and denominator must be specified",
    "c_ratio_denominator_missing": "Both a numerator and denominator must be specified",
}
#: Each entry dbt accepts and writes without the key that makes its row: the key under
#: `type_params`, written as null.
DROPPED = {
    "c_params_cumulative_type_params": "cumulative_type_params",
    "c_v2_params_window": "window",
    "c_v2_params_grain_to_date": "grain_to_date",
    "c_v2_fill_nulls_with_0": "fill_nulls_with",
    "c_v2_fill_nulls_with_1": "fill_nulls_with",
}

Triple = tuple[str, str, str | None]
#: Whether a gap is a row's, from the resolver's reason and the value the artifact has at the
#: gap's path.
Tell = Callable[[str, Any], bool]


class _ParityRow(NamedTuple):
    """A row of the parity matrix, `<engine>.<event time>:<form>`: a ratio's form has two
    terms, a simple metric's one, each `<dialect>.<aggregate>.<class of the column's type>`; a
    legacy metric whose measure has no `expr`, or which has an `expr` of its own, has a row of
    its own besides, `legacy.measure_without_expr` or `legacy.metric_expr`."""

    engine: str
    time: str
    ratio: bool
    terms: tuple[tuple[str, ...], ...]


#: A term of a form, as the resolver writes it.
_TERM = r"(?:legacy|v2)\.\w+\.\w+"
#: A row of the parity matrix: its engine and event time, each a word, and its form. The
#: resolver writes the engine as it is declared, lower-cased; the corpus declares `postgres`.
_MATRIX_ROW = re.compile(
    rf"(\w+)\.(\w+):"
    rf"(?:ratio\.({_TERM})/({_TERM})|({_TERM}|legacy\.(?:measure_without_expr|metric_expr)))"
)


def _says(pattern: str) -> Tell:
    """The reason is `pattern`, whole."""
    compiled = re.compile(pattern)
    return lambda reason, _value: compiled.fullmatch(reason) is not None


def _construct(what: str) -> Tell:
    """The reason names the construct `what`."""
    return _says(re.escape(CONSTRUCT + what))


def _holds(test: Callable[[Any], bool]) -> Tell:
    """The value at the gap's path passes `test`."""
    return lambda _reason, value: test(value)


def _sets(key: str, default: Any) -> Tell:
    """The `agg_params` at the gap's path set `key` to other than its default."""
    return _holds(lambda params: params.get(key, default) != default)


def _parity(test: Callable[[_ParityRow], bool]) -> Tell:
    """A row of the matrix the reason names passes `test`. A reason that is not, whole, rows
    of the matrix as `_MATRIX_ROW` reads them is no row."""

    def tell(reason: str, _value: Any) -> bool:
        if not reason.startswith(PARITY):
            return False
        rows = []
        for key in reason.removeprefix(PARITY).split(", "):
            found = _MATRIX_ROW.fullmatch(key)
            if found is None:
                return False
            engine, time, top, bottom, term = found.groups()
            terms = (top, bottom) if term is None else (term,)
            split = tuple(tuple(part.split(".")) for part in terms)
            rows.append(_ParityRow(engine, time, term is None, split))
        return any(test(row) for row in rows)

    return tell


#: What tells the row a gap is from the other rows of the inventory with its code, severity and
#: path shape: the resolver's reason, or, where the reason is one too — a time dimension's
#: `expr` and its name, the keys of `agg_params` — the value the artifact has at the gap's path.
#: Each row of a group some gap of the record is in.
TELLS: dict[str, Tell] = {
    "time.expression": _holds(lambda expr: expr is not None),
    "time.name": _holds(lambda expr: expr is None),
    "ratio.models": _says(
        r"the numerator reads semantic model '\w+' and the denominator '\w+'; "
        r"a typed ratio has one table"
    ),
    "ratio.times": _says(
        "the numerator and the denominator aggregate over different time dimensions"
    ),
    **{
        f"type.{kind}": _construct(f"a {kind} metric")
        for kind in ("cumulative", "derived", "conversion")
    },
    "ratio.input-type": _says(re.escape(CONSTRUCT) + r"a ratio over a \w+ metric"),
    **{f"ratio.{side}-missing": _construct(f"a ratio without a {side}") for side in SIDES},
    "params.numerator": _construct("a numerator"),
    "params.denominator": _construct("a denominator"),
    "params.expr": _construct("an expression"),
    "legacy.metric-expr": _construct("a metric expression other than the measure's"),
    "v2.no-expr": _construct("a V2 metric without an expression"),
    "v2.expr": _construct("a column that is not one lower-case identifier"),
    **{
        f"{dialect}.agg.{agg}": _construct(f"aggregation {agg}")
        for dialect in ("legacy", "v2")
        for agg in ("sum_boolean", "percentile", "median")
    },
    **{
        f"{dialect}.agg_params.{key}": _sets(key, default)
        for dialect in ("legacy", "v2")
        for key, default in (
            ("percentile", None),
            ("use_discrete_percentile", False),
            ("use_approximate_percentile", False),
        )
    },
    "parity.engine": _parity(lambda row: row.engine not in RUN_ENGINES),
    "parity.event-time-type": _parity(lambda row: row.time not in ("date", OUTSIDE_GRAIN)),
    "parity.event-time-outside-grain": _parity(lambda row: row.time == OUTSIDE_GRAIN),
    "parity.column-type": _parity(
        lambda row: any(len(term) == 3 and term[2] not in RUN_CLASSES for term in row.terms)
    ),
    "parity.ratio-average": _parity(
        lambda row: row.ratio and any(term[1:2] == ("average",) for term in row.terms)
    ),
    "parity.ratio-float": _parity(
        lambda row: row.ratio
        and any(
            term[1:] in {("sum", "float"), ("min", "float"), ("max", "float")} for term in row.terms
        )
    ),
    "parity.v2": _parity(lambda row: any(term[0] == "v2" for term in row.terms)),
}


def _script() -> ModuleType:
    module = sys.modules.get("dbt_corpus")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "dbt_corpus", REPO / "scripts" / "dbt_corpus.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # A dataclass looks its module up while it is made.
        sys.modules["dbt_corpus"] = module
        spec.loader.exec_module(module)
    return module


SCRIPT = _script()
RESULT: dict[str, Any] = json.loads((CORPUS / "result.json").read_bytes())
RAW = {
    dialect: (CORPUS / dialect / "semantic_manifest.json").read_bytes()
    for dialect in SCRIPT.DIALECTS
}
ARTIFACTS: dict[str, Any] = {dialect: json.loads(raw) for dialect, raw in RAW.items()}
ENTRIES = {entry.metric: entry for entry in SCRIPT.ENTRIES}
ACCEPTED = [entry for entry in SCRIPT.ENTRIES if "refused" not in RESULT["outcomes"][entry.metric]]
#: The entries of the corpus of each dialect.
ACCEPTED_IN = {
    dialect: [entry for entry in ACCEPTED if entry.dialect == dialect]
    for dialect in SCRIPT.DIALECTS
}


def _read(dialect: str) -> ManifestRead:
    read = read_semantic_manifest(RAW[dialect])
    assert read.manifest is not None, read.problems
    return read


def _row(ident: str) -> Triple:
    row = ROWS[ident]
    return row.code, row.severity, row.path


def _triple(gap: dict[str, str]) -> Triple:
    return gap["code"], gap["severity"], _shape(gap["path"])


def _node(data: Any, path: str) -> Any:
    """The value at a path of the resolver's, `$.metrics[3].type_params.expr`."""
    assert path.startswith("$."), path
    for name, index in re.findall(r"\.([a-z_]+)|\[(\d+)\]", path[1:]):
        data = data[name] if name else data[int(index)]
    return data


def _metric(dialect: str, name: str) -> dict[str, Any]:
    metrics = json.loads(RAW[dialect])["metrics"]
    (metric,) = (metric for metric in metrics if metric["name"] == name)
    return dict(metric)


def _group(triple: Triple) -> set[str]:
    """The rows of the inventory with a gap's code, severity and path shape."""
    return {ident for ident in ROWS if _row(ident) == triple}


def _told(gap: dict[str, str], dialect: str) -> set[str]:
    """The rows a gap of the artifact of `dialect` is: of the rows with its code, severity and
    path shape, the one, or each `TELLS` finds."""
    group = _group(_triple(gap))
    if len(group) < 2:
        return group
    value = _node(ARTIFACTS[dialect], gap["path"])
    return {ident for ident in group if TELLS[ident](gap["reason"], value)}


def _verdict(entry: Any) -> str:
    """`refused`; `carries` when each gap of the metric is a row the entry lists, and each row
    it lists a gap's; `misses` otherwise."""
    outcome = RESULT["outcomes"][entry.metric]
    if "refused" in outcome:
        return "refused"
    told = [_told(gap, entry.dialect) for gap in outcome.get("gaps", [])]
    if all(told) and set[str]().union(*told) == {*entry.aims, *entry.also}:
        return "carries"
    return "misses"


def _classes() -> dict[str, list[str]]:
    """Each row by what dbt-core 1.12.6 writes for the entries built for it."""
    verdicts = {entry.metric: _verdict(entry) for entry in SCRIPT.ENTRIES}
    written = {
        ident
        for entry in SCRIPT.ENTRIES
        if verdicts[entry.metric] == "carries"
        for ident in (*entry.aims, *entry.also)
    }
    aimed: dict[str, list[str]] = {}
    for entry in SCRIPT.ENTRIES:
        for ident in entry.aims:
            aimed.setdefault(ident, []).append(verdicts[entry.metric])
    classes: dict[str, list[str]] = {name: [] for name in CLASSES}
    for ident in ROWS:
        if ident in written:
            classes["Written"].append(ident)
        elif ident not in aimed:
            classes["No entry"].append(ident)
        elif set(aimed[ident]) == {"refused"}:
            classes["Refused"].append(ident)
        else:
            classes["Not carried"].append(ident)
    return classes


def _spec_classes() -> dict[str, list[str]]:
    """The lists of the section: a paragraph `**Class** (count) — …`, then one of the ids."""
    text = SPEC.read_text(encoding="utf-8")
    assert text.count(f"\n{SECTION}\n") == 1
    paragraphs = text.split(f"\n{SECTION}\n", 1)[1].split("\n## ", 1)[0].split("\n\n")
    found = []
    for at, paragraph in enumerate(paragraphs):
        head = re.match(r"\*\*([A-Za-z ]+)\*\* \((\d+)\) ", paragraph)
        if head is not None:
            found.append((head.group(1), int(head.group(2)), paragraphs[at + 1]))
    assert [name for name, _count, _ids in found] == list(CLASSES)
    classes: dict[str, list[str]] = {}
    for name, count, ids in found:
        assert re.fullmatch(r"`[a-z0-9_.\-]+`(?:,[ \n]`[a-z0-9_.\-]+`)*\.", ids), ids
        classes[name] = re.findall(r"`([a-z0-9_.\-]+)`", ids)
        assert len(classes[name]) == count, name
    return classes


# --- the record ---------------------------------------------------------------------------


def test_the_record_is_of_this_recipe_and_these_versions() -> None:
    assert RESULT["recipe"] == SCRIPT.recipe(ACCEPTED_IN)
    assert RESULT["versions"] == {
        "dbt-core": "1.12.6",
        "dbt-postgres": "1.11.0",
        "metricflow": "0.213.0",
    }


@pytest.mark.parametrize(
    "changed",
    [lambda entries: not entries, lambda entries: len(entries) > 1],
    ids=["the base", "the corpus"],
)
def test_a_project_dbt_parses_only_as_the_base_or_the_corpus_is_in_the_recipe(
    changed: Callable[[Sequence[Any]], bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    built, unchanged = SCRIPT.project_files, SCRIPT.recipe(ACCEPTED_IN)

    def project_files(dialect: str, entries: Sequence[Any]) -> dict[str, str]:
        files = built(dialect, entries)
        if changed(entries):
            files["models/schema.yml"] += "\n"
        return files

    monkeypatch.setattr(SCRIPT, "project_files", project_files)
    assert SCRIPT.recipe(ACCEPTED_IN) != unchanged


def test_the_artifacts_are_the_recorded_ones() -> None:
    assert RESULT["artifacts"] == {dialect: digest_bytes(raw) for dialect, raw in RAW.items()}
    for dialect in SCRIPT.DIALECTS:
        assert _read(dialect).manifest is not None


def test_the_record_has_each_entry_and_each_control() -> None:
    controls = {f"{dialect}:{name}" for dialect in SCRIPT.DIALECTS for name in SCRIPT.CONTROLS}
    assert set(RESULT["outcomes"]) == set(ENTRIES) | controls
    assert len(ENTRIES) == len(SCRIPT.ENTRIES)


def test_entries_name_rows_of_the_inventory() -> None:
    for entry in SCRIPT.ENTRIES:
        assert entry.aims, entry.metric
        assert set(entry.aims) | set(entry.also) <= set(ROWS), entry.metric
        assert not set(entry.aims) & set(entry.also), entry.metric
        assert entry.dialect in SCRIPT.DIALECTS


def test_an_entry_dbt_refused_is_in_no_artifact_and_keeps_dbts_reason() -> None:
    names = {dialect: json.loads(RAW[dialect])["metrics"] for dialect in SCRIPT.DIALECTS}
    names = {dialect: {metric["name"] for metric in metrics} for dialect, metrics in names.items()}
    for entry in SCRIPT.ENTRIES:
        outcome = RESULT["outcomes"][entry.metric]
        if "refused" in outcome:
            assert outcome["refused"].startswith(("[WARNING]", "[ERROR]")), entry.metric
            assert all(entry.metric not in found for found in names.values())
        else:
            assert entry.metric in names[entry.dialect]


def test_the_controls_resolve_in_legacy_and_stop_at_the_granularity_in_v2() -> None:
    for name in SCRIPT.CONTROLS:
        assert set(RESULT["outcomes"][f"legacy:{name}"]) == {"formula"}
        gaps = RESULT["outcomes"][f"v2:{name}"]["gaps"]
        assert [_triple(gap) for gap in gaps] == [_row(SCRIPT.NO_GRANULARITY)]


@pytest.mark.parametrize("entry", ACCEPTED, ids=lambda entry: entry.metric)
def test_an_accepted_entry_resolves_as_recorded(entry: Any) -> None:
    got = SCRIPT.outcome(_read(entry.dialect), entry.metric, entry.table, entry.additivity)
    assert got == RESULT["outcomes"][entry.metric]


@pytest.mark.parametrize("dialect", ("legacy", "v2"))
def test_a_control_resolves_as_recorded(dialect: str) -> None:
    for name in SCRIPT.CONTROLS:
        got = SCRIPT.outcome(_read(dialect), name, SCRIPT.TABLE)
        assert got == RESULT["outcomes"][f"{dialect}:{name}"]


# --- what dbt does with a construct -------------------------------------------------------


def test_dbt_refuses_each_entry_for_the_construct_it_carries() -> None:
    refused = {name for name, outcome in RESULT["outcomes"].items() if "refused" in outcome}
    assert refused == set(REFUSALS)
    for name, reason in REFUSALS.items():
        assert reason in RESULT["outcomes"][name]["refused"], name


def test_an_entry_misses_its_row_only_where_dbt_drops_the_key_or_stops_v2_short() -> None:
    missing = {entry.metric for entry in SCRIPT.ENTRIES if _verdict(entry) == "misses"}
    # `approximate_continuous` sets neither key: test_a_v2_percentile_type_sets_one_key_or_neither
    assert missing == set(DROPPED) | {"c_parity_v2", "c_v2_percentile_type_other"}
    for name, key in DROPPED.items():
        entry = ENTRIES[name]
        params = entry.add["metrics"][-1].get("type_params", entry.add["metrics"][-1])
        assert params[key] is not None, name
        assert _metric(entry.dialect, name)["type_params"][key] is None, name
    # Parity is asked of a formula with no other gap, and no V2 metric gets that far.
    assert [_triple(gap) for gap in RESULT["outcomes"]["c_parity_v2"]["gaps"]] == [
        _row(SCRIPT.NO_GRANULARITY)
    ]


def test_a_v2_percentile_type_sets_one_key_or_neither() -> None:
    written = {}
    for name in (
        "c_v2_agg_percentile",
        "c_v2_agg_params_use_discrete_percentile",
        "c_v2_agg_params_use_approximate_percentile",
        "c_v2_percentile_type_other",
    ):
        authored = ENTRIES[name].add["metrics"][0].get("percentile_type")
        params = _metric("v2", name)["type_params"]["metric_aggregation_params"]["agg_params"]
        keys = ("use_discrete_percentile", "use_approximate_percentile")
        written[authored] = tuple(params[key] for key in keys)
        told = [_told(gap, "v2") for gap in RESULT["outcomes"][name]["gaps"]]
        assert "v2.agg.percentile" in set[str]().union(*told), name
    assert written == {
        None: (False, False),
        "discrete": (True, False),
        "continuous": (False, True),
        "approximate_continuous": (False, False),
    }


def test_the_flag_keeps_window_and_grain_to_date_at_the_top_of_type_params() -> None:
    for key in ("window", "grain_to_date"):
        refused, kept = ENTRIES[f"c_params_{key}"], ENTRIES[f"c_params_{key}_flag"]
        assert refused.flags == {} and kept.flags == UNNESTED
        assert _verdict(refused) == "refused" and _verdict(kept) == "carries"
        assert _metric("legacy", kept.metric)["type_params"][key] is not None
    assert "flags:\n  require_nested_cumulative_type_params: false\n" in (
        SCRIPT.project_files("legacy", [ENTRIES["c_params_window_flag"]])["dbt_project.yml"]
    )


def test_a_name_sql_reads_as_a_value_is_taken_and_given_its_row() -> None:
    legacy = json.loads(RAW["legacy"])
    measures = {m["name"]: m for model in legacy["semantic_models"] for m in model["measures"]}
    assert measures["true"]["expr"] is None
    assert _verdict(ENTRIES["c_legacy_measure_name_2"]) == "carries"
    (model,) = (m for m in legacy["semantic_models"] if m["name"] == "c_time_name_2")
    assert [(d["name"], d["type"], d["expr"]) for d in model["dimensions"]] == [
        ("current_date", "time", None)
    ]
    assert _verdict(ENTRIES["c_time_name_2"]) == "carries"


def test_no_v2_metric_resolves() -> None:
    v2 = json.loads(RAW["v2"])
    times = [
        d for model in v2["semantic_models"] for d in model["dimensions"] if d["type"] == "time"
    ]
    assert times and all(dimension["type_params"] is None for dimension in times)
    for entry in ACCEPTED:
        if entry.dialect == "v2":
            outcome = RESULT["outcomes"][entry.metric]
            assert "formula" not in outcome, entry.metric
            gaps = {_triple(gap) for gap in outcome["gaps"]}
            stops = _row(SCRIPT.NO_GRANULARITY) in gaps
            assert stops != (entry.aims == ("time.none-v2",)), entry.metric
            assert _row("parity.v2") not in gaps, entry.metric


# --- the rows -----------------------------------------------------------------------------


def test_the_rows_told_apart_are_each_of_a_group_a_gap_of_the_record_is_in() -> None:
    gaps = [gap for outcome in RESULT["outcomes"].values() for gap in outcome.get("gaps", [])]
    shared = {ident for ident in ROWS if len(_group(_row(ident))) > 1}
    given = {ident for gap in gaps for ident in _group(_triple(gap))}
    assert set(TELLS) == shared & given
    # the one group no gap is in: dbt refuses each entry built for its rows
    assert shared - given == {"ratio.input-measures", "legacy.input-measures", "v2.input-measures"}


def test_a_gap_rows_share_is_some_of_them_and_not_all() -> None:
    for entry in ACCEPTED:
        for gap in RESULT["outcomes"][entry.metric].get("gaps", []):
            group, told = _group(_triple(gap)), _told(gap, entry.dialect)
            assert told, (entry.metric, gap["path"])
            assert len(group) == 1 or told < group, (entry.metric, gap["path"])


def test_a_gap_that_is_no_row_keeps_its_entry_from_carrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = ENTRIES["c_metric_filter"]
    gaps = RESULT["outcomes"][entry.metric]["gaps"]
    assert _verdict(entry) == "carries"
    row = ROWS["parity.v2"]
    proven = {
        "code": row.code,
        "severity": row.severity,
        "path": gaps[0]["path"].removesuffix(".filter"),
        "reason": PARITY + "postgres.date:legacy.sum.decimal",
    }
    assert _told(proven, entry.dialect) == set()
    monkeypatch.setitem(RESULT["outcomes"], entry.metric, {"gaps": [*gaps, proven]})
    assert _verdict(entry) == "misses"


def test_a_gap_of_a_row_the_entry_does_not_list_keeps_it_from_carrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = ENTRIES["c_metric_filter"]
    gaps = RESULT["outcomes"][entry.metric]["gaps"]
    assert _verdict(entry) == "carries"
    (granularity,) = RESULT["outcomes"]["c_metric_time_granularity"]["gaps"]
    assert _told(granularity, entry.dialect) == {"metric.time_granularity"}
    monkeypatch.setitem(RESULT["outcomes"], entry.metric, {"gaps": [*gaps, granularity]})
    assert _verdict(entry) == "misses"


def _recorded(ident: str) -> tuple[dict[str, str], str]:
    """A gap of the record that is the row `ident`, and the dialect of its artifact."""
    for entry in ACCEPTED:
        for gap in RESULT["outcomes"][entry.metric].get("gaps", []):
            if ident in _told(gap, entry.dialect):
                return gap, entry.dialect
    raise AssertionError(f"no gap of the record is {ident}")


@pytest.mark.parametrize(
    "ident", ["type.cumulative", "ratio.input-type", "ratio.models", "parity.ratio-average"]
)
@pytest.mark.parametrize(
    "said",
    [lambda reason: "not " + reason, lambda reason: reason + " over a window"],
    ids=["before", "after"],
)
def test_a_reason_tells_its_row_only_whole(ident: str, said: Callable[[str], str]) -> None:
    gap, dialect = _recorded(ident)
    assert _told({**gap, "reason": said(gap["reason"])}, dialect) == set()


@pytest.mark.parametrize(
    ("named", "rows"),
    [
        # the one the record holds
        ("postgres.date:ratio.legacy.average.decimal/legacy.sum.decimal", {"parity.ratio-average"}),
        ("postgres.date:ratio.legacy.count.float/legacy.average.float", {"parity.ratio-average"}),
        ("postgres.date:ratio.legacy.sum.decimal/legacy.max.float", {"parity.ratio-float"}),
        ("postgres.date:legacy.sum.text", {"parity.column-type"}),
        ("postgres.date:legacy.max.date", {"parity.column-type"}),
        ("postgres.date:legacy.min.undeclared", {"parity.column-type"}),
        ("postgres.timestamp:legacy.sum.decimal", {"parity.event-time-type"}),
        (
            f"postgres.{OUTSIDE_GRAIN}:legacy.measure_without_expr",
            {"parity.event-time-outside-grain"},
        ),
        (
            "clickhouse.unclassified:legacy.sum.unclassified",
            {"parity.event-time-type", "parity.column-type"},
        ),
        (
            "greenplum.unclassified:legacy.sum.unclassified",
            {"parity.engine", "parity.event-time-type", "parity.column-type"},
        ),
        (
            "undeclared.undeclared:legacy.count.undeclared",
            {"parity.engine", "parity.event-time-type", "parity.column-type"},
        ),
        ("postgres.date:v2.sum.decimal", {"parity.v2"}),
        (
            "postgres.date:legacy.sum.text,"
            " postgres.date:ratio.legacy.min.float/legacy.sum.decimal",
            {"parity.column-type", "parity.ratio-float"},
        ),
        ("greenplum.date:legacy.metric_expr", {"parity.engine"}),
        ("greengage.date:legacy.sum.decimal", {"parity.engine"}),
        # an engine the run proved rows on is not what keeps a row unproven
        ("clickhouse.date:legacy.metric_expr", set()),
        ("clickhouse.date:ratio.legacy.sum.decimal/legacy.max.float", {"parity.ratio-float"}),
        # what the resolver does not write names no row of the matrix
        ("postgres.date:ratio.legacy.average", set()),
        ("postgres.date:ratio.legacy.sum.float", set()),
        ("postgres.date:ratio.legacy.sum.float/legacy.sum.decimal/legacy.sum.decimal", set()),
        ("postgres.date:ratio.legacy.metric_expr/legacy.sum.float", set()),
        ("postgres.date:v2.sum", set()),
        ("postgres.date:v2.sum.decimal.text", set()),
        ("postgres.date:v3.sum.text", set()),
        ("postgres.date:legacy.sum.text and more", set()),
        ("postgres:legacy.sum.text", set()),
        ("postgres.date:legacy.sum.text; postgres.date:legacy.max.date", set()),
        ("postgres.date:legacy.sum.text, postgres.date:ratio.legacy.average", set()),
        # nor does an engine or an event time that is not a word, though the resolver writes
        # an engine as it is declared
        ("custom-engine.unclassified:legacy.sum.unclassified", set()),
        ("postgres.date-time:legacy.sum.decimal", set()),
    ],
)
def test_a_parity_gap_is_each_row_a_matrix_row_it_names_holds(named: str, rows: set[str]) -> None:
    row = ROWS["parity.v2"]
    gap = {
        "code": row.code,
        "severity": row.severity,
        "path": "$.metrics[0]",
        "reason": PARITY + named,
    }
    assert _told(gap, "legacy") == rows


def test_the_spec_sorts_the_rows_as_the_record_does() -> None:
    spec = _spec_classes()
    assert spec == _classes()
    listed = [ident for name in CLASSES for ident in spec[name]]
    assert sorted(listed) == sorted(ROWS)
    for name in CLASSES:
        assert spec[name] == [ident for ident in ROWS if ident in spec[name]], name


# --- the script ---------------------------------------------------------------------------


def test_the_excerpt_keeps_dbts_reasons_and_nothing_of_the_run(tmp_path: Path) -> None:
    where = tmp_path / "c_metric"
    long = "x" * 400
    output = "\n".join(
        [
            "12:00:01  Running with dbt=1.12.6",
            f"12:00:02  Registered adapter: postgres=1.11.0 at {where}",
            "12:00:03  [WARNING]: Metric 'c_metric' is odd",
            "",
            f"12:00:04  [ERROR]: in {where}/models/schema.yml",
            "  Metric(name='c_metric', created_at=1794059509.546174, group=None)",
            "  FilePath(searched_path='models', relative_path='schema.yml',"
            " modification_time=1794059509.5, project_root='/somewhere')",
            "  Invalid metrics config given in models/schema.yml @ metrics: {'name': 'c_metric'}"
            " - at path []: no",
            f"  {long}",
            *[f"  line {index}" for index in range(10)],
        ]
    )
    assert SCRIPT._excerpt(output, where).split("\n") == [
        "[WARNING]: Metric 'c_metric' is odd",
        "[ERROR]: in <project>/models/schema.yml",
        "  Metric(name='c_metric', created_at=…, group=None)",
        "  models/schema.yml",
        "  Invalid metrics config given in models/schema.yml @ metrics: {…} - at path []: no",
        f"  {'x' * 148} … {'x' * 150}",
        *[f"  line {index}" for index in range(6)],
    ]


def _dbt_as_recorded(
    monkeypatch: pytest.MonkeyPatch, alone: Callable[[str, Any], None] = lambda _where, _data: None
) -> list[str]:
    """Have the script's dbt answer as the record says it did: the recorded error for a project
    dbt refused, the corpus of its dialect for one it accepted — where the project is not the
    corpus, with its metrics and models reversed, so its paths have other indexes, and changed
    by `alone`. The names of the projects parsed, in order."""
    parsed: list[str] = []

    def parse(_dbt: str, files: dict[str, str], where: Path) -> tuple[bytes | None, str]:
        parsed.append(where.name)
        if where.name in RAW:
            assert files == SCRIPT.project_files(where.name, ACCEPTED_IN[where.name])
            return RAW[where.name], ""
        entries: list[Any] = []
        if where.name.startswith("base_"):
            dialect = where.name.removeprefix("base_")
        else:
            entry = ENTRIES[where.name]
            dialect, entries = entry.dialect, [entry]
            if "refused" in RESULT["outcomes"][entry.metric]:
                return None, RESULT["outcomes"][entry.metric]["refused"]
        assert files == SCRIPT.project_files(dialect, entries)
        data = json.loads(RAW[dialect])
        data["metrics"].reverse()
        data["semantic_models"].reverse()
        alone(where.name, data)
        return json.dumps(data).encode(), ""

    monkeypatch.setattr(SCRIPT, "_parse", parse)
    monkeypatch.setattr(SCRIPT, "_versions", lambda _dbt: RESULT["versions"])
    return parsed


def test_a_run_on_what_dbt_wrote_writes_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _dbt_as_recorded(monkeypatch)
    assert SCRIPT.run("dbt", tmp_path) == 0
    assert parsed == ["base_legacy", "base_v2", *ENTRIES, "legacy", "v2"]
    for name in ("result.json", "legacy/semantic_manifest.json", "v2/semantic_manifest.json"):
        assert (tmp_path / name).read_bytes() == (CORPUS / name).read_bytes(), name


def _unfiltered(data: Any) -> None:
    (metric,) = (metric for metric in data["metrics"] if metric["name"] == "c_metric_filter")
    metric["filter"] = None


def _revenue_max(data: Any) -> None:
    (model,) = (model for model in data["semantic_models"] if model["name"] == "sales_daily")
    (measure,) = (measure for measure in model["measures"] if measure["name"] == "revenue")
    measure["agg"] = "max"


@pytest.mark.parametrize(
    ("where", "change", "moved"),
    [
        ("c_metric_filter", _unfiltered, "c_metric_filter"),
        ("base_legacy", _revenue_max, "legacy:revenue"),
    ],
)
def test_a_run_stops_where_a_metric_resolves_otherwise_on_its_own_project(
    where: str,
    change: Callable[[Any], None],
    moved: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def alone(at: str, data: Any) -> None:
        if at == where:
            change(data)

    _dbt_as_recorded(monkeypatch, alone)
    with pytest.raises(SystemExit, match=re.escape(moved)):
        SCRIPT.run("dbt", tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_the_record_keeps_nothing_of_the_run() -> None:
    trace = re.compile(r"created_at=\d|\b\d\d:\d\d:\d\d\b|/(?:tmp|var|private|Users|home)/")
    for name, outcome in RESULT["outcomes"].items():
        if "refused" in outcome:
            assert trace.search(outcome["refused"]) is None, name
