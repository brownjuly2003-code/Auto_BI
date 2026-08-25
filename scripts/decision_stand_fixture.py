"""Lay out a decision-product fixture on disk, to run the gate against a live stand (manual).

NOT part of CI, and not a test fixture: the suite builds its own inputs in `tmp_path`. This is
for the other check — pointing `auto_bi decision verify --dwh …` at a real engine and reading
what step 7 says about it. Writing that by hand is a dozen YAML files, and a hand-written one
drifts from what the tests actually cover; this emits exactly the shapes
`tests/test_decision_verify.py` uses, so a live run and the suite are arguing about one product.

Usage::

    uv run python scripts/decision_stand_fixture.py <dir> <engine> [budget=N ...]

`asset=REGION:RATIO,...` writes one verified asset (`weekly_margin_by_region`) whose expected
result is those rows, pinned to the base snapshot laid out here; `results=REGION:RATIO,...`
writes the candidate result recorded for it, so `results.reconcile` has something to compare.
The gate never runs the asset query itself — record the rows from the stand.

`--two-products` lays a second product (`weekly_revenue`, metric `revenue`) beside the margin
product, on the same mart — the graph on which the gate has to tell one product's change from
the other's.

`<engine>` is what the model declares for its tables (`clickhouse`, `greenplum`) — it decides
which dialect step 6 compiles the reference query for, and a plan compiled for another engine is
not asked of the one the probe is connected to. Every `budget=N` becomes a `budgets` key on the
metric contract (`max_scanned_rows`, `max_scanned_bytes`, `timeout_ms`); with none, the metric
declares no ceiling and `dwh.cost` answers `cost.no_budget_declared`.

The stand the fixture expects is the one `docs/DECISIONOPS_CURRENT_STATE.md` describes under
"Стенды, на которых это проверялось": a `dm.sales_daily` seeded with 600 rows over the last 30
days. The directory is rewritten from scratch on every run, so point it somewhere disposable.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

from auto_bi.semantic.gateway import NativeModelSource

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # the fixture shapes live beside the tests that assert them
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_decision_verify import (  # noqa: E402
    dashboard_spec,
    metric_contract,
    product_contract,
    recorded_results,
    revenue_metric,
    revenue_product,
    semantic_model,
    verified_asset,
)

USAGE = (
    "usage: decision_stand_fixture.py <dir> <engine> [--two-products] "
    "[asset=REGION:RATIO,...] [results=REGION:RATIO,...] [max_scanned_rows=N] [timeout_ms=N] ..."
)
ROWS_KEYS = ("asset", "results")


def _rows(spec: str) -> list[list[str]]:
    """`msk:0.452054,kzn:0.377` -> rows of the asset result (region, gross_margin_pct)."""
    return [item.split(":", 1) for item in spec.split(",") if item]


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _lf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    root, engine = Path(argv[0]), argv[1]
    rest = [item for item in argv[2:] if item != "--two-products"]
    two_products = len(rest) != len(argv[2:])
    try:
        pairs = [item.split("=", 1) for item in rest]
        rows = {key: _rows(value) for key, value in pairs if key in ROWS_KEYS}
        budgets = {key: int(value) for key, value in pairs if key not in ROWS_KEYS}
    except ValueError:
        print(USAGE, file=sys.stderr)
        return 2

    contract = metric_contract()
    if budgets:
        contract["budgets"] = budgets

    if root.exists():
        shutil.rmtree(root)
    _write(root / "contracts" / "weekly_margin.yaml", product_contract())
    _write(root / "contracts" / "gross_margin_pct.yaml", contract)
    for ref in ("base", "candidate"):  # unchanged semantics: the run is about the environment
        model = semantic_model()
        for table in model.tables:
            if table.physical is not None:
                table.physical.engine = engine
        model.dump(root / ref / "model.yaml")
        _lf(root / ref / "model.yaml")  # v1 dump() writes CRLF on Windows
        _write(root / ref / "metrics" / "gross_margin_pct.yaml", contract)
        if two_products:
            _write(root / ref / "metrics" / "revenue.yaml", revenue_metric())
    _write(root / "dashboards" / "weekly_margin@7.yaml", dashboard_spec())
    if two_products:
        # A second product on the same mart and a different metric: the graph on which
        # "reaches A, not B" is a claim and not a tautology.
        _write(root / "contracts" / "revenue.yaml", revenue_metric())
        _write(root / "contracts" / "weekly_revenue.yaml", revenue_product())
        _write(root / "dashboards" / "weekly_revenue@1.yaml", dashboard_spec())
    if "asset" in rows:
        # The asset is pinned to the base snapshot it was reviewed against, so the digest is
        # computed from what was just written, not typed in.
        base = NativeModelSource(root / "base" / "model.yaml", root / "base" / "metrics")
        asset = verified_asset(base.snapshot().canonical_digest)
        asset["expectation"]["result"]["rows"] = rows["asset"]
        _write(root / "assets" / "weekly_margin_by_region.yaml", asset)
    if "results" in rows:
        # What the candidate answered for that asset — recorded by whoever ran it (here, a
        # query against the stand), never computed by the gate.
        _write(root / "results.yaml", recorded_results(rows["results"]))

    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
