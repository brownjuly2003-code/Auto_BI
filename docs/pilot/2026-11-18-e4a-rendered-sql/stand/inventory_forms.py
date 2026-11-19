"""E4a: what the inventory reader v1 makes of each stand dashboard (typed / read SQL / refused).

Runs in the project's Mac venv against the repo checkout (/tmp/autobi_t47/repo), as the stand admin.
The model is the corpus model of tests/test_superset_inventory.py built from the live answers:
every physical dataset with its plain columns. No secret is printed.
Usage: venv/bin/python inventory_forms.py 41|61 OUT.json [DASHBOARD_ID ...]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/autobi_t47/repo")

from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.inventory.superset import (
    RestSupersetSource,
    SupersetAnswerError,
    SupersetSourceError,
    inventory_dashboard,
)
from auto_bi.semantic.model import Column, ColumnRole, SemanticModel, Table

STAND = Path(os.environ.get("E4A_STAND", Path.home() / "e4astand"))
ENV = dict(line.split("=", 1) for line in (STAND / ".env").read_text().splitlines() if "=" in line)
version, out = sys.argv[1], Path(sys.argv[2])
base = "http://127.0.0.1:" + ("8088" if version == "41" else "8089")
client = SupersetClient(base, ENV["AUTO_BI_SUPERSET_USER"], ENV["AUTO_BI_SUPERSET_PASSWORD"])
source = RestSupersetSource(client)
listed = client.get("/api/v1/dashboard/", params={"q": "(page_size:100)"})["result"]
idents = [int(a) for a in sys.argv[3:]] or sorted(int(d["id"]) for d in listed)

NUMERIC = ("INT", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL", "REAL")
tables: dict[str, Table] = {}
for ident in idents:
    try:
        datasets = source.datasets(str(ident))
    except (SupersetSourceError, SupersetAnswerError):
        continue
    for dataset in datasets:
        if dataset.get("sql") or not dataset.get("schema"):
            continue
        name = f"{dataset['schema']}.{dataset['table_name']}"
        columns = []
        for column in dataset.get("columns") or []:
            if column.get("expression"):
                continue
            kind = (column.get("type") or "").upper()
            role = (
                ColumnRole.TIME
                if column.get("is_dttm")
                else (
                    ColumnRole.MEASURE
                    if any(word in kind for word in NUMERIC)
                    else ColumnRole.DIMENSION
                )
            )
            columns.append(Column(name=column["column_name"], type=kind or "STRING", role=role))
        if columns:
            tables.setdefault(name, Table(name=name, columns=columns))
model = SemanticModel(tables=list(tables.values()))

result: dict[str, object] = {}
for ident in idents:
    try:
        read = inventory_dashboard(f"dashboard://d{ident}@1", str(ident), source, model)
    except (SupersetSourceError, SupersetAnswerError) as error:
        result[str(ident)] = {"error": f"{type(error).__name__}: {error}"}
        continue
    result[str(ident)] = {
        "written": read.spec is not None,
        "refusals": read.record.refusals,
        "charts": [
            {"id": c.superset_id, "viz": c.viz_type, "form": str(c.form), "reasons": c.reasons}
            for c in read.record.charts
        ],
    }
out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
forms = [c["form"] for r in result.values() if "charts" in r for c in r["charts"]]  # type: ignore[index]
print(
    "dashboards",
    len(idents),
    "written",
    sum(1 for r in result.values() if r.get("written")),
    "errors",
    sum(1 for r in result.values() if "error" in r),
    "typed",
    forms.count("typed"),
    "read_sql",
    forms.count("read_sql"),
)
