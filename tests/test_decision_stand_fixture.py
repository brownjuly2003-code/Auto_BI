"""`scripts/decision_stand_fixture.py` offline: what it lays out for the live checks.

The live files (`tests/test_decision_live_clickhouse.py`, `tests/test_decision_live_postgres.py`)
run it only where a server is given; these keep its options from breaking unseen in between.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "decision_stand_fixture", ROOT / "scripts" / "decision_stand_fixture.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_results_are_written_as_the_candidate_recorded_them(tmp_path: Path) -> None:
    stand = tmp_path / "stand"

    laid = _fixture().main(
        [str(stand), "clickhouse", "asset=msk:0.45,kzn:0.37", "results=msk:0.45,kzn:0.38"]
    )

    assert laid == 0
    results = _yaml(stand / "results.yaml")
    assert isinstance(results, dict)
    [recorded] = results.values()
    assert recorded["rows"] == [["msk", "0.45"], ["kzn", "0.38"]]


def test_a_postgres_kernel_stand_gets_its_types_no_layout_and_its_engine(tmp_path: Path) -> None:
    fixture = _fixture()
    stand = tmp_path / "stand"

    assert fixture.main([str(stand), "postgres", "asset=msk:0.45"]) == 0

    model = _yaml(stand / "base" / "model.yaml")
    assert isinstance(model, dict)
    types = {column["type"] for table in model["tables"] for column in table["columns"]}
    assert types and types <= set(fixture.POSTGRES_TYPES.values())
    for table in model["tables"]:
        assert table["physical"] == {"engine": "postgres"}
    asset = _yaml(stand / "assets" / "weekly_margin_by_region.yaml")
    assert isinstance(asset, dict)
    assert asset["reference_query"]["engine"] == "postgres"
