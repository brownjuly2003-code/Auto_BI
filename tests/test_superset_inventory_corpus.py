"""The Superset inventory reader on the pinned E4a replay corpus (ADR 0013, E4b).

The corpus is `tests/fixtures/superset_inventory/e4a/`: per dashboard of an E4a record, every
answer the reader can get by `GET` and the reference — the SQL the dashboard showed for each tile
in a browser, checked against the engine log. `scripts/superset_inventory_measure.py` replays the
answers to the reader and reads every tile of a written spec the way the gate reads it.

The numbers below are the threshold of the reader 2, recorded with the reader 1 base before the
reader 2 code (`docs/pilot/2026-11-19-e4b-inventory-reads/README.md`). Superset 4.1.2 — the pin —
holds the threshold; Superset 6.1.0 is measured for information and only must not regress or
miss a read.
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

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "fixtures" / "superset_inventory" / "e4a"

#: The corpus is pinned: a changed answer is a new corpus, not a new number.
DIGESTS = {
    "41-c1rest": "f7f7ed7f26aebeec6c659b398635226f5e145ce922790bc145913cef1906cac6",
    "41-c1w": "64bbe84e7e94c32aaeab788b7ca619dd521747efaa21f35ed68ea8e7f13a4ae4",
    "41-c2w": "ce2ecc22d36d5f58898c15a2e916570f184da8f24e403e49795b6786d9a29ed9",
    "61-c1w": "8062600bef17227953bd4bd51a905ca4403a09f09f24e0c4fa91e1b08a75544a",
    "61-c2w": "62958898aa8202f0c4daa6b9b8b33fe65583084b99298520ba2e4dd94996ec0d",
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


#: Recorded before the reader 2 code: reader 1 reads no tile from rendered SQL. The reader 2
#: slice removes the marks.
_BEFORE_READER_2 = pytest.mark.xfail(strict=True, reason="reader 2 not written yet")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("41-c1rest", marks=_BEFORE_READER_2),
        "41-c1w",
        pytest.param("41-c2w", marks=_BEFORE_READER_2),
    ],
)
def test_reader_2_reads_the_area_from_rendered_sql_on_the_pin(name: str) -> None:
    summary, rows = _run(name)
    rendered, whole = THRESHOLD[name]
    assert {r["chart"] for r in rows if r["form"] == "rendered_sql"} == rendered
    assert summary["counts"]["tiles_whole"] == whole


@pytest.mark.parametrize("name", ["61-c1w", "61-c2w"])
def test_superset_6_1_does_not_regress(name: str) -> None:
    summary, _ = _run(name)
    assert summary["counts"]["tiles_whole"] >= BASE[name][3]
