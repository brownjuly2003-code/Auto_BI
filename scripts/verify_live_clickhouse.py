"""Live ClickHouse verification of the deterministic numeric / SQL-gen paths (manual, stand-only).

NOT part of CI (CI is hermetic): this needs the Mac ClickHouse stand. It runs the ACTUAL
``generate_chart_sql`` ClickHouse output against the live stand and asserts it element-wise
against an independent base GROUP BY — the check that caught real ClickHouse-only bugs the offline
(DuckDB/Postgres) tests cannot: a grain alias-shadow ``NOT_AN_AGGREGATE``, a ``Decimal/Decimal``
truncation, a ``lagInFrame`` out-of-frame NULL default. Read-only; ClickHouse credentials are read
from ``.env`` and never printed.

Usage (with the stand reachable)::

    uv run python scripts/verify_live_clickhouse.py

Stand access defaults to the ssh host alias ``deproject-mac`` and the ``auto_bi_clickhouse``
container (as elsewhere in this repo); override with AUTO_BI_VERIFY_SSH_HOST /
AUTO_BI_VERIFY_CH_CONTAINER. The docker binary is addressed by full path because it is not on the
non-interactive ssh PATH. Exits non-zero on any mismatch and exits 2 when the stand is unreachable.

Covers: ratio measure (num/den), time_grain (month buckets, week = Monday), yoy_pct, mom
(grain + pop), lag_periods (pop_pct vs N periods back), running_share (Pareto cumulative share,
window ordered by the measure), histogram (equal-width binning of a numeric column), and the
auto-overview (real model.yaml -> build_auto_spec -> the dynamics line is a readable monthly
trend whose totals match the live data).
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
from collections.abc import Callable
from datetime import date

from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import ChartQuery, Measure, MeasureTransform, OrderBy, TimeGrain, Viz
from auto_bi.semantic.model import Aggregation, SemanticModel

REPO = pathlib.Path(__file__).resolve().parent.parent
SSH_HOST = os.environ.get("AUTO_BI_VERIFY_SSH_HOST", "deproject-mac")
CH_CONTAINER = os.environ.get("AUTO_BI_VERIFY_CH_CONTAINER", "auto_bi_clickhouse")
TABLE = "dm.sales_daily"

Runner = Callable[[str], list[list]]

# the independent monthly series (no transform / grain magic), reused by several checks
_MONTHLY_SQL = (
    'SELECT toStartOfMonth("date") AS m, toFloat64(SUM("revenue")) AS r '
    "FROM dm.sales_daily GROUP BY m ORDER BY m"
)


def _dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _make_runner(env: dict[str, str]) -> Runner:
    remote = (
        f"/usr/local/bin/docker exec -i {CH_CONTAINER} clickhouse-client "
        f"--user {env['AUTO_BI_CH_USER']} --password {env['AUTO_BI_CH_PASSWORD']} "
        f"--database {env['AUTO_BI_CH_DATABASE']} --format JSONCompactEachRow"
    )

    def run(sql: str) -> list[list]:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", SSH_HOST, remote],
            input=sql.encode("utf-8"),
            capture_output=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:600])
        out = proc.stdout.decode("utf-8").strip()
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    return run


def _approx(a: float | str | None, b: float | str | None, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def _check(failures: list[str], name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def _sum_revenue(**extra: object) -> Measure:
    return Measure(column="revenue", agg=Aggregation.SUM, **extra)  # type: ignore[arg-type]


def _verify_trio(ch: Runner, failures: list[str]) -> None:
    print(f"\n[trio] ratio / time_grain / yoy_pct / mom on {TABLE}")

    # ratio: SUM(revenue) / SUM(orders) by date — a divide-by-zero day yields NULL
    ratio = _sum_revenue(denominator=Measure(column="orders", agg=Aggregation.SUM))
    q = ChartQuery(
        table=TABLE, dimensions=["date"], measures=[ratio], order_by=[OrderBy(by="date")]
    )
    gen = ch(generate_chart_sql(q, apply_limit=False))
    ind = ch(
        'SELECT "date", toFloat64(SUM("revenue")) AS r, SUM("orders") AS o '
        'FROM dm.sales_daily GROUP BY "date" ORDER BY "date"'
    )
    exp = [None if float(i[2]) == 0 else float(i[1]) / float(i[2]) for i in ind]
    ok = len(gen) == len(ind) and all(
        g[0] == i[0] and _approx(g[1], e) for g, i, e in zip(gen, ind, exp, strict=True)
    )
    _check(failures, "ratio = SUM(revenue)/SUM(orders) by date", ok, f"{len(gen)} dates")

    # month grain: SUM(revenue) truncated to month; reuse the monthly series for yoy/mom
    base = ChartQuery(
        table=TABLE,
        dimensions=["date"],
        measures=[_sum_revenue()],
        time_grain=TimeGrain.MONTH,
        order_by=[OrderBy(by="date")],
    )
    gm = ch(generate_chart_sql(base, apply_limit=False))
    im = ch(_MONTHLY_SQL)
    months = [float(r[1]) for r in im]
    n = len(months)
    ok = len(gm) == len(im) and all(
        g[0] == i[0] and date.fromisoformat(g[0]).day == 1 and _approx(g[1], i[1])
        for g, i in zip(gm, im, strict=True)
    )
    _check(failures, "month grain: 1st-of-month buckets, totals match", ok, f"{n} months")

    # week grain: every bucket must be a Monday (ClickHouse toStartOfWeek mode 1)
    week = base.model_copy(update={"time_grain": TimeGrain.WEEK})
    gw = ch(generate_chart_sql(week, apply_limit=False))
    ok = bool(gw) and all(date.fromisoformat(r[0]).weekday() == 0 for r in gw)
    _check(failures, "week grain: buckets start on Monday", ok, f"{len(gw)} weeks")

    # yoy_pct: first year NULL (CH toNullable), then (v - v_-12) / v_-12
    yoy = base.model_copy(update={"measures": [_sum_revenue(transform=MeasureTransform.YOY_PCT)]})
    gy = ch(generate_chart_sql(yoy, apply_limit=False))
    exp_y = [None] * 12 + [(months[k] - months[k - 12]) / months[k - 12] for k in range(12, n)]
    ok = (
        len(gy) == len(exp_y)
        and all(g[1] is None for g in gy[:12])
        and all(_approx(g[1], e) for g, e in zip(gy, exp_y, strict=True))
    )
    _check(failures, "yoy_pct: first year NULL, rest match hand calc", ok, f"{len(gy)} months")

    # mom = month grain + pop_pct: first NULL, then (v - v_-1) / v_-1
    mom = base.model_copy(update={"measures": [_sum_revenue(transform=MeasureTransform.POP_PCT)]})
    gmom = ch(generate_chart_sql(mom, apply_limit=False))
    exp_m = [None] + [(months[k] - months[k - 1]) / months[k - 1] for k in range(1, n)]
    ok = (
        len(gmom) == len(exp_m)
        and gmom[0][1] is None
        and all(_approx(g[1], e) for g, e in zip(gmom, exp_m, strict=True))
    )
    _check(failures, "mom (grain+pop_pct): first NULL, rest match", ok, f"{len(gmom)} months")

    # lag_periods (vs N periods back): pop_pct at month grain, lag 3 -> (v - v_-3) / v_-3, first
    # 3 NULL. Same frame-bounded lagInFrame as yoy but a caller-chosen offset (Measure.lag_periods)
    lag3 = base.model_copy(
        update={"measures": [_sum_revenue(transform=MeasureTransform.POP_PCT, lag_periods=3)]}
    )
    glag = ch(generate_chart_sql(lag3, apply_limit=False))
    exp_l = [None] * 3 + [(months[k] - months[k - 3]) / months[k - 3] for k in range(3, n)]
    ok = (
        len(glag) == len(exp_l)
        and all(g[1] is None for g in glag[:3])
        and all(_approx(g[1], e) for g, e in zip(glag, exp_l, strict=True))
    )
    _check(
        failures,
        "lag_periods=3 (pop_pct vs 3 months back): first 3 NULL, rest match",
        ok,
        f"{len(glag)} months",
    )


def _verify_running_share(ch: Runner, failures: list[str]) -> None:
    print(f"\n[running_share] Pareto / ABC cumulative share over store_id on {TABLE}")
    # cumulative share of revenue by store, ranked by the measure descending; the generated
    # window orders by the aggregate value, NOT a time axis — a NEW SQL construct (not the yoy
    # lag), so it needs its own live check.
    q = ChartQuery(
        table=TABLE,
        dimensions=["store_id"],
        measures=[_sum_revenue(transform=MeasureTransform.RUNNING_SHARE)],
    )
    gen = {r[0]: r[1] for r in ch(generate_chart_sql(q, apply_limit=False))}
    # independent: per-store revenue, ranked desc, cumulative / grand total (store sums are
    # large distinct figures over 20M rows -> no ties to make the ROWS-frame order ambiguous)
    ind = ch(
        'SELECT "store_id", toFloat64(SUM("revenue")) AS r '
        'FROM dm.sales_daily GROUP BY "store_id" ORDER BY r DESC'
    )
    total = sum(float(r[1]) for r in ind)
    running = 0.0
    expected: dict[object, float] = {}
    for store_id, rev in ind:
        running += float(rev)
        expected[store_id] = running / total
    ok = (
        len(gen) == len(expected)
        and all(_approx(gen.get(k), v) for k, v in expected.items())
        and _approx(max(gen.values()), 1.0)  # the cumulative share closes at exactly 1.0
    )
    _check(
        failures,
        "running_share: cumulative share by store matches, closes at 1.0",
        ok,
        f"{len(gen)} stores",
    )


def _verify_histogram(ch: Runner, failures: list[str]) -> None:
    print("\n[histogram] equal-width binning of dm.products.price")
    # bin product prices into 8 buckets; the generated SQL uses a CROSS JOIN to a min/max
    # subquery + a bucket expression with the binned column qualified by the base table — a NEW
    # SQL shape (least/floor/NULLIF, alias-shadow risk), so it needs its own live check.
    bins = 8
    q = ChartQuery(
        table="dm.products",
        dimensions=["price"],
        measures=[Measure(column="price", agg=Aggregation.COUNT)],
        bins=bins,
    )
    gen = ch(generate_chart_sql(q, apply_limit=False))  # (bucket_lower_bound, count) rows
    # independent: recompute buckets in Python from the raw prices
    prices = [float(r[0]) for r in ch('SELECT toFloat64("price") FROM dm.products')]
    mn, mx = min(prices), max(prices)
    width = (mx - mn) / bins
    counts: dict[float, int] = {}
    for p in prices:
        idx = min(int((p - mn) // width), bins - 1) if width else 0
        lb = mn + idx * width
        counts[round(lb, 6)] = counts.get(round(lb, 6), 0) + 1
    exp_sorted = sorted(counts.items())
    ok = (
        len(gen) == len(exp_sorted)
        # CH returns UInt64 counts as strings in JSON -> int() before summing
        and sum(int(r[1]) for r in gen) == len(prices)  # every product lands in one bucket
        and all(
            _approx(g[0], e[0]) and int(g[1]) == e[1]
            for g, e in zip(sorted(gen, key=lambda r: float(r[0])), exp_sorted, strict=True)
        )
    )
    _check(
        failures,
        "histogram: bucket bounds + counts match hand calc, all rows binned",
        ok,
        f"{len(gen)} buckets / {len(prices)} products",
    )


def _verify_autospec(ch: Runner, failures: list[str]) -> None:
    print("\n[autospec] auto-overview time-views (real model.yaml)")
    model = SemanticModel.load(REPO / "semantic" / "model.yaml")
    spec = build_auto_spec(model, TABLE)
    lines = [c for c in spec.charts if c.viz == Viz.LINE]
    ind = ch(_MONTHLY_SQL)
    months = [float(r[1]) for r in ind]

    # the absolute dynamics line: the hero measure as a readable monthly trend
    dyn = next(c for c in lines if not any(m.transform for m in c.query.measures))
    print(f"  dynamics: {dyn.title!r}  time_grain={dyn.query.time_grain}")
    rows = ch(generate_chart_sql(dyn.query, apply_limit=False))
    ok = (
        dyn.query.time_grain == TimeGrain.MONTH
        and len(rows) == len(ind)
        and all(date.fromisoformat(r[0]).day == 1 for r in rows)
        and all(_approx(g[1], i[1]) for g, i in zip(rows, ind, strict=True))
    )
    _check(failures, "dynamics line is a monthly trend matching live CH", ok, f"{len(rows)} points")

    # the year-over-year line (added when there are 2+ years of history): same hero measure, but
    # each month vs the same month a year back — first year NULL, the rest a hand-checkable ratio
    yoy = None
    for c in lines:
        if any(m.transform == MeasureTransform.YOY_PCT for m in c.query.measures):
            yoy = c
            break
    if yoy is None:
        _check(failures, "auto-overview emits a year-over-year line", False, "no yoy line built")
        return
    print(f"  yoy: {yoy.title!r}  time_grain={yoy.query.time_grain}")
    gy = ch(generate_chart_sql(yoy.query, apply_limit=False))
    n = len(months)
    exp = [None] * 12 + [(months[k] - months[k - 12]) / months[k - 12] for k in range(12, n)]
    ok = (
        yoy.query.time_grain == TimeGrain.MONTH
        and len(gy) == len(exp)
        and all(g[1] is None for g in gy[:12])
        and all(_approx(g[1], e) for g, e in zip(gy, exp, strict=True))
    )
    _check(failures, "auto-overview yoy line matches hand calc on live CH", ok, f"{len(gy)} months")


def main() -> int:
    ch = _make_runner(_dotenv())
    try:
        ch("SELECT 1")
    except Exception as exc:
        print(f"stand unreachable via ssh {SSH_HOST!r} -> docker {CH_CONTAINER!r}: {exc}")
        print("bring the stand up and retry; this script needs the live ClickHouse.")
        return 2

    failures: list[str] = []
    _verify_trio(ch, failures)
    _verify_running_share(ch, failures)
    _verify_histogram(ch, failures)
    _verify_autospec(ch, failures)

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: FAIL ({len(failures)} check(s)):")
        for name in failures:
            print("  -", name)
        return 1
    print("RESULT: ALL CHECKS PASS — deterministic CH paths verified on the live stand")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
