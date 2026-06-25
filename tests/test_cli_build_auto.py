"""CLI `build --auto <table>` dispatch: deterministic auto-overview, no LLM/DWH.

The autospec logic is covered by test_autospec.py; this locks only the CLI wiring —
that `--auto` routes to `build_auto_spec` and feeds the result to `compile_and_build`,
with nothing connecting to a DWH/BI (the heavy collaborators are faked).
"""

import pytest

import auto_bi.adapters.factory as factory
import auto_bi.agent.pipeline as pipeline
import auto_bi.introspect.clickhouse as ch
import auto_bi.store as store_mod
from auto_bi.adapters.base import DashboardRef
from auto_bi.cli import main

MODEL = "semantic/model.yaml"  # the committed demo model (repo root is pytest cwd)


class _FakeStore:
    def __init__(self, *a, **k) -> None: ...

    def create_session(self, desc):
        return "sess-1"

    def save_spec(self, session_id, spec):
        return 1


def test_build_auto_routes_to_autospec(monkeypatch, capsys) -> None:
    captured: dict = {}

    def fake_compile(spec, model, sql_validator, adapter_for, *a, **k):
        captured["spec"] = spec
        return DashboardRef(id="d1", title=spec.title, url="/superset/dashboard/99/")

    monkeypatch.setattr(pipeline, "compile_and_build", fake_compile)
    monkeypatch.setattr(ch, "make_run_query", lambda settings: (lambda *a, **k: []))
    monkeypatch.setattr(factory, "make_adapter", lambda *a, **k: object())
    monkeypatch.setattr(store_mod, "Store", _FakeStore)

    rc = main(["build", "--auto", "dm.sales_daily", "--model-path", MODEL])

    assert rc == 0
    spec = captured["spec"]
    assert spec.title.startswith("Обзор:")  # produced by build_auto_spec, not the LLM
    assert spec.charts
    assert all(c.query.table == "dm.sales_daily" for c in spec.charts)
    out = capsys.readouterr().out
    assert "Авто-обзор" in out and "Дашборд готов" in out


def test_build_auto_respects_max_charts(monkeypatch) -> None:
    captured: dict = {}

    def fake_compile(spec, *a, **k):
        captured["spec"] = spec
        return DashboardRef(id="d1", title=spec.title, url="/x")

    monkeypatch.setattr(pipeline, "compile_and_build", fake_compile)
    monkeypatch.setattr(ch, "make_run_query", lambda settings: (lambda *a, **k: []))
    monkeypatch.setattr(factory, "make_adapter", lambda *a, **k: object())
    monkeypatch.setattr(store_mod, "Store", _FakeStore)

    rc = main(["build", "--auto", "dm.sales_daily", "--model-path", MODEL, "--max-charts", "3"])

    assert rc == 0
    assert len(captured["spec"].charts) == 3


def test_build_auto_unknown_table_returns_2(capsys) -> None:
    # build_auto_spec raises before any DWH collaborator is touched; nothing to fake
    rc = main(["build", "--auto", "dm.nope", "--model-path", MODEL])
    assert rc == 2
    assert "Авто-режим" in capsys.readouterr().out


def test_build_without_description_or_auto_errors() -> None:
    with pytest.raises(SystemExit):
        main(["build"])
