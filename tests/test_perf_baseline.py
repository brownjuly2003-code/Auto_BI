"""plan_sol step 12: offline performance baseline + soft relative budgets.

Measures pure local work only (no DWH, no BI, no LLM, no Docker). Absolute
ceilings are intentionally generous so CI runners do not flake; relative ratios
catch structural regressions (O(n^2) blow-ups). Memory stays tiny — synthetic
models, not full demo DM.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.normalize import apply_chart_defaults
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import (
    Aggregation,
    Column,
    ColumnRole,
    Physical,
    SemanticModel,
    Table,
)
from auto_bi.store import Store

# Soft absolute ceilings (ms). Catastrophic regression only — not a tight SLO.
_ABS_AUTOSPEC_MS = 3_000.0
_ABS_VALIDATE_MS = 2_000.0
_ABS_STORE_N_MS = 5_000.0  # N session+spec writes
_ABS_BACKUP_MS = 5_000.0

# Relative: large synthetic must not explode vs small
_REL_AUTOSPEC_RATIO = 40.0
_REL_VALIDATE_RATIO = 40.0

_STORE_N = 80
_PERF_DIR = Path(__file__).resolve().parent / "performance"
_BASELINE_JSON = _PERF_DIR / "last_local_baseline.json"


def _synthetic_model(*, n_dims: int, n_measures: int, table: str = "dm.fact") -> SemanticModel:
    cols = [
        Column(name="date", type="Date", role=ColumnRole.TIME, description="Day"),
    ]
    card: dict[str, int] = {}
    for i in range(n_dims):
        name = f"dim_{i}"
        cols.append(
            Column(
                name=name,
                type="LowCardinality(String)",
                role=ColumnRole.DIMENSION,
                description=f"Dimension {i}",
            )
        )
        card[name] = 5 + (i % 20)
    for i in range(n_measures):
        name = f"m_{i}"
        cols.append(
            Column(
                name=name,
                type="Float64",
                role=ColumnRole.MEASURE,
                agg=Aggregation.SUM,
                description=f"Measure {i}",
            )
        )
    return SemanticModel(
        tables=[
            Table(
                name=table,
                description="Synthetic fact",
                grain=["date"] + [f"dim_{i}" for i in range(min(3, n_dims))],
                columns=cols,
                physical=Physical(engine="MergeTree", cardinality=card),
            )
        ]
    )


def _ms(fn, *args, **kwargs) -> tuple[float, object]:
    t0 = time.perf_counter()
    result = fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000.0, result


def test_autospec_and_validate_budgets() -> None:
    small = _synthetic_model(n_dims=3, n_measures=2)
    large = _synthetic_model(n_dims=25, n_measures=8)

    s_ms, s_spec = _ms(build_auto_spec, small, "dm.fact")
    l_ms, l_spec = _ms(build_auto_spec, large, "dm.fact")

    assert s_ms < _ABS_AUTOSPEC_MS, f"small autospec {s_ms:.1f}ms >= {_ABS_AUTOSPEC_MS}"
    assert l_ms < _ABS_AUTOSPEC_MS, f"large autospec {l_ms:.1f}ms >= {_ABS_AUTOSPEC_MS}"
    # relative: avoid O(n^2) surprise (floor small ms so ratio is stable)
    floor = max(s_ms, 0.05)
    assert (
        l_ms / floor <= _REL_AUTOSPEC_RATIO
    ), f"autospec ratio large/small = {l_ms / floor:.1f} > {_REL_AUTOSPEC_RATIO}"

    s_spec = apply_chart_defaults(s_spec, small)
    l_spec = apply_chart_defaults(l_spec, large)
    sv_ms, s_errs = _ms(validate_spec, s_spec, small)
    lv_ms, l_errs = _ms(validate_spec, l_spec, large)
    assert s_errs == []
    assert l_errs == []
    assert sv_ms < _ABS_VALIDATE_MS
    assert lv_ms < _ABS_VALIDATE_MS
    v_floor = max(sv_ms, 0.05)
    assert lv_ms / v_floor <= _REL_VALIDATE_RATIO

    # Optional local artifact (not a CI golden — machine-specific timings)
    _PERF_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "autospec_small_ms": round(s_ms, 3),
        "autospec_large_ms": round(l_ms, 3),
        "validate_small_ms": round(sv_ms, 3),
        "validate_large_ms": round(lv_ms, 3),
        "charts_small": len(s_spec.charts),
        "charts_large": len(l_spec.charts),
    }
    _BASELINE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_sqlite_session_write_and_backup_budgets(tmp_path: Path) -> None:
    path = tmp_path / "perf.sqlite"
    store = Store(path)
    try:
        t0 = time.perf_counter()
        for i in range(_STORE_N):
            sid = store.create_session(f"req-{i}")
            store.save_spec(sid, {"title": f"t{i}", "charts": []})
            store.add_message(sid, "user", f"m{i}")
        write_ms = (time.perf_counter() - t0) * 1000.0
        assert write_ms < _ABS_STORE_N_MS, f"store writes {write_ms:.1f}ms"

        dest = tmp_path / "perf-backup.sqlite"
        b_ms, _ = _ms(store.backup_to, dest)
        assert b_ms < _ABS_BACKUP_MS, f"backup {b_ms:.1f}ms"
        assert store.is_integrity_ok()
    finally:
        store.close()

    restored = Store(tmp_path / "perf-backup.sqlite")
    try:
        n = restored._rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
        assert n == _STORE_N
        assert restored.is_integrity_ok()
    finally:
        restored.close()


def test_autospec_output_bounded_by_max_charts() -> None:
    """Structural SLO: curated overview never dumps unbounded charts."""
    model = _synthetic_model(n_dims=40, n_measures=12)
    for max_charts in (4, 8, 12):
        spec = build_auto_spec(model, "dm.fact", max_charts=max_charts)
        assert len(spec.charts) <= max_charts
