"""CLI `introspect` dispatch: --engine routes to the matching introspector.

The introspector logic is covered by test_introspect*.py; these lock only the CLI
wiring added for first-class Greenplum onboarding, with no live DWH (the factory and
introspector are faked, so nothing connects).
"""

from pathlib import Path

import auto_bi.introspect.clickhouse as ch
import auto_bi.introspect.greenplum as gp
from auto_bi.cli import main
from auto_bi.config import get_settings


class _FakeModel:
    def __init__(self, tables: list) -> None:
        self.tables = tables

    def dump(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("fake-model", encoding="utf-8")


class _FakeTable:
    columns = ("a", "b")


def _install_fake(monkeypatch, module, cls_name: str, make_name: str, record: dict) -> None:
    class FakeIntrospector:
        def __init__(self, run_query, schema=None) -> None:
            record["constructed"] = cls_name
            record["schema"] = schema

        def introspect(self, database=None):
            record["database"] = database
            return _FakeModel([_FakeTable(), _FakeTable()])

    monkeypatch.setattr(module, make_name, lambda settings: (lambda *a, **k: []))
    monkeypatch.setattr(module, cls_name, FakeIntrospector)


def test_introspect_routes_to_greenplum(monkeypatch, tmp_path) -> None:
    record: dict = {}
    _install_fake(monkeypatch, gp, "GreenplumIntrospector", "make_run_query_pg", record)
    out = tmp_path / "model_gp.yaml"

    rc = main(["introspect", "--engine", "greenplum", "--output", str(out)])

    assert rc == 0
    assert record["constructed"] == "GreenplumIntrospector"
    assert record["schema"] == get_settings().gp_schema  # GP path passes the configured schema
    assert out.read_text(encoding="utf-8") == "fake-model"


def test_introspect_defaults_to_clickhouse(monkeypatch, tmp_path) -> None:
    record: dict = {}
    _install_fake(monkeypatch, ch, "ClickHouseIntrospector", "make_run_query", record)
    out = tmp_path / "model.yaml"

    rc = main(["introspect", "--output", str(out)])

    assert rc == 0
    assert record["constructed"] == "ClickHouseIntrospector"
    assert out.read_text(encoding="utf-8") == "fake-model"
