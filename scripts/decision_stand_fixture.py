"""Lay out a decision-product fixture on disk, to run the gate against a live stand (manual).

NOT part of CI, and not a test fixture: the suite builds its own inputs in `tmp_path`. This is
for the other check — pointing `auto_bi decision verify --dwh …` at a real engine and reading
what step 7 says about it. Writing that by hand is a dozen YAML files, and a hand-written one
drifts from what the tests actually cover; this emits exactly the shapes
`tests/test_decision_verify.py` uses, so a live run and the suite are arguing about one product.

Usage::

    uv run python scripts/decision_stand_fixture.py <dir> <engine> [budget=N ...]

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

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # the fixture shapes live beside the tests that assert them
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_decision_verify import (  # noqa: E402
    dashboard_spec,
    metric_contract,
    product_contract,
    semantic_model,
)

USAGE = "usage: decision_stand_fixture.py <dir> <engine> [max_scanned_rows=N] [timeout_ms=N] ..."


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    root, engine = Path(argv[0]), argv[1]
    try:
        budgets = {key: int(value) for key, value in (item.split("=") for item in argv[2:])}
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
        _write(root / ref / "metrics" / "gross_margin_pct.yaml", contract)
    _write(root / "dashboards" / "weekly_margin@7.yaml", dashboard_spec())

    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
