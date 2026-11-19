"""The Superset inventory reader on the pinned E4a replay corpus (ADR 0013, E4b).

The corpus is `tests/fixtures/superset_inventory/e4a/`: per dashboard of an E4a record, every
answer the reader can get by `GET` and the reference — the SQL the dashboard showed for each tile
in a browser, checked against the engine log. `scripts/superset_inventory_measure.py` replays the
answers to the reader and reads every tile of a written spec the way the gate reads it.

The numbers below are the thresholds of the reader 2 (E4b-1) and of composite SQL (E4b-2), each
recorded with its base before its code (`docs/pilot/2026-11-19-e4b-inventory-reads/README.md`).
Superset 4.1.2 — the pin — holds the thresholds; Superset 6.1.0 is measured for information and
only must not regress or miss a read.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from auto_bi.inventory import superset as reader

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "fixtures" / "superset_inventory" / "e4a"

#: The corpus is pinned: a changed answer is a new corpus, not a new number.
DIGESTS = {
    "41-c1rest": "917382e3b83435560b975716303fb663fd277aa7c0aea7f38e6edbfca3e43545",
    "41-c1w": "dc4d5eaddbb4aaa06a93b578bf9901bde58d34b2a47a45a13aff2e05757904ae",
    "41-c2w": "5ff54a626b08c1765a32256a71beef8062fdf17ebf46b07a09e3f47c8b871b21",
    "61-c1w": "aebc2e7676d26c2b9fb607d0310a9a9ab30f0715e5ad7659c2981e0562e4d629",
    "61-c2w": "567aaa7d819c514b6c03350b6016c66aa4764bd0f120c7acf324559b256476dd",
}

#: Reader 1 on the corpus: dashboards written / refused, typed tiles, tiles read whole.
BASE = {
    "41-c1w": (9, 2, 5, 5),
    "41-c2w": (8, 2, 6, 6),
    "41-c1rest": (1, 1, 0, 0),
    "61-c1w": (7, 2, 1, 1),
    "61-c2w": (8, 2, 4, 4),
}

#: Reader 2 on Superset 4.1.2: the tiles read from rendered SQL — exactly the charts of the
#: E4a area on a written dashboard that reader 1 does not type — and the tiles read whole.
THRESHOLD = {
    "41-c1w": (frozenset(), 5),
    "41-c2w": (frozenset({139, 145}), 8),
    "41-c1rest": (frozenset({12, 15, 17}), 3),
}

#: Reader 2 (E4b-1, `928e140`) on the corpus: tiles read whole.
READER_2_WHOLE = {"41-c1w": 5, "41-c2w": 8, "41-c1rest": 3, "61-c1w": 1, "61-c2w": 8}

#: E4b-2 candidates on 4.1.2, written before the code: tiles of written dashboards that reader 2
#: leaves over-read, measured against the engine, of a viz in the 4.1.2 chart/data list of the
#: E4a `api` check. Legacy viz (`viz.py`) are not candidates.
CANDIDATES = {"41-c1w": 39, "41-c2w": 25, "41-c1rest": 4}

#: Of the 68 candidates, at least this many are read whole from composite SQL.
COMPOSITE_WHOLE = 60

_E4B2 = pytest.mark.xfail(strict=True, reason="E4b-2: composite SQL is not implemented yet")


@cache
def _measure() -> ModuleType:
    module = sys.modules.get("superset_inventory_measure")
    if module is None:
        spec = importlib.util.spec_from_file_location(
            "superset_inventory_measure", REPO / "scripts" / "superset_inventory_measure.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["superset_inventory_measure"] = module
        spec.loader.exec_module(module)
    return module


@cache
def _run(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    run = json.loads((CORPUS / f"{name}.json").read_bytes())
    summary, rows = _measure().measure_run(run)
    return summary, rows


@pytest.mark.parametrize("name", sorted(DIGESTS))
def test_the_corpus_is_the_pinned_one(name: str) -> None:
    assert hashlib.sha256((CORPUS / f"{name}.json").read_bytes()).hexdigest() == DIGESTS[name]


@pytest.mark.parametrize("name", sorted(BASE))
def test_no_tile_read_whole_misses_a_read_of_the_dashboard(name: str) -> None:
    summary, rows = _run(name)
    counts = summary["counts"]
    assert counts["tiles_whole_missed"] == 0, [r for r in rows if r["missed"]]
    # Every tile read whole is measured: the claim is checked against the engine, not assumed.
    assert counts["tiles_whole_measured"] == counts["tiles_whole"], [
        r for r in rows if r["whole"] and not r["measured"]
    ]


@pytest.mark.parametrize("name", sorted(BASE))
def test_dashboards_and_typed_tiles_stay_as_reader_1_had_them(name: str) -> None:
    summary, _ = _run(name)
    counts = summary["counts"]
    written, refused, typed, _whole = BASE[name]
    assert (
        counts.get("dashboards_written", 0),
        counts.get("dashboards_refused", 0),
        counts.get("tiles_typed", 0),
    ) == (written, refused, typed)


@pytest.mark.parametrize("name", sorted(THRESHOLD))
def test_reader_2_reads_the_area_from_rendered_sql_on_the_pin(name: str) -> None:
    summary, rows = _run(name)
    rendered, whole = THRESHOLD[name]
    assert {r["chart"] for r in rows if r["form"] == "rendered_sql"} == rendered
    # Exact for reader 2; a later reader may only add to it.
    assert summary["counts"]["tiles_whole"] >= whole


@pytest.mark.parametrize("name", ["61-c1w", "61-c2w"])
def test_superset_6_1_does_not_regress(name: str) -> None:
    summary, _ = _run(name)
    assert summary["counts"]["tiles_whole"] >= READER_2_WHOLE[name]


def _candidate(row: dict[str, Any]) -> bool:
    return row["measured"] and row["viz"] in reader._RENDERED_VIZ


@pytest.mark.parametrize("name", sorted(CANDIDATES))
def test_the_e4b2_candidates_are_the_recorded_ones(name: str) -> None:
    _, rows = _run(name)
    left = [r for r in rows if r["form"] in {"read_sql", "composite_sql"} and _candidate(r)]
    assert len(left) == CANDIDATES[name]


@_E4B2
def test_composite_sql_reads_whole_at_least_60_of_the_68_candidates() -> None:
    whole = sum(
        r["whole"] for name in CANDIDATES for r in _run(name)[1] if r["form"] == "composite_sql"
    )
    assert whole >= COMPOSITE_WHOLE
    assert sum(_run(name)[0]["counts"]["tiles_whole"] for name in CANDIDATES) >= 16 + whole


@_E4B2
@pytest.mark.parametrize("name", sorted(CANDIDATES))
def test_a_candidate_left_over_read_names_why(name: str) -> None:
    _, rows = _run(name)
    for row in rows:
        if row["form"] == "read_sql" and _candidate(row):
            assert any(reason.startswith("composite_sql:") for reason in row["reasons"]), row


@pytest.mark.parametrize("name", sorted(BASE))
def test_composite_sql_is_only_for_modern_viz_and_legacy_viz_stay_over_read(name: str) -> None:
    _, rows = _run(name)
    for row in rows:
        if row["viz"] not in reader._RENDERED_VIZ:
            assert row["form"] in {"typed", "read_sql"}, row
        if row["form"] == "composite_sql":
            assert row["viz"] in reader._RENDERED_VIZ, row
