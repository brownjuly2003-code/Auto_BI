"""Golden-path demo: the whole IR-first pipeline on synthetic data, no stand, no LLM.

Run from a clean clone to see, in one command, what Auto_BI does end-to-end:

    uv run python scripts/demo_golden_path.py

It walks the deterministic pipeline against the committed demo model
(`semantic/model.yaml`, a 20M-row ClickHouse sales mart):

  1. Semantic model      - what introspection grounded the agent on (roles, joins, stats)
  2. Auto-overview spec   - a curated dashboard built from the model alone (no LLM)
  3. Compiled SQL         - the validated SQL the deterministic compiler emits per chart
  4. Feasibility Advisor  - the engine-aware verdict on a request the mart can't serve

Only the final BUILD step (HTTP to Superset/DataLens, EXPLAIN+LIMIT on the live stand)
needs the running stand - that is `auto_bi build --auto dm.sales_daily --target superset`.
Everything this script prints is produced offline, from the repository alone.
"""

from __future__ import annotations

import argparse

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table as RichTable

from auto_bi.advisor.core import Advisor
from auto_bi.agent.autospec import build_auto_spec
from auto_bi.agent.normalize import apply_chart_defaults, apply_label_joins
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    Aggregation,
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    FilterOp,
    Measure,
    QueryFilter,
    TargetBI,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Column, SemanticModel

console = Console()


def _act(n: int, title: str) -> None:
    console.print()
    console.print(Rule(f"[bold]{n}. {title}", style="cyan"))


def show_model(model: SemanticModel) -> None:
    _act(1, "Semantic model — what the agent is grounded on")
    table = RichTable(show_lines=False)
    table.add_column("table")
    table.add_column("rows", justify="right")
    table.add_column("columns (role)")
    for t in model.tables:
        rows = f"{t.physical.rows:,}" if t.physical and t.physical.rows else "—"
        cols = ", ".join(f"{c.name}·{c.role.value[:3]}" for c in t.columns)
        table.add_row(t.name, rows, cols)
    console.print(table)
    if model.joins:
        edges = ", ".join(f"{j.left} → {j.right}" for j in model.joins)
        console.print(f"[dim]join edges: {edges}[/dim]")
    console.print(
        "[dim]Roles (time/dimension/measure), foreign keys and physical stats come from "
        "`auto_bi introspect` — the agent only ever generates IR validated against this.[/dim]"
    )


def show_auto_overview(model: SemanticModel, table_name: str) -> DashboardSpec:
    _act(2, f"Auto-overview — a curated dashboard for `{table_name}` (deterministic, no LLM)")
    spec = build_auto_spec(model, table_name, target_bi=TargetBI.SUPERSET)

    # the exact normalization the build pipeline runs before SQL_GEN + the adapter
    labeled = apply_label_joins(spec, model)
    relabeled = [
        c.id for o, c in zip(spec.charts, labeled.charts, strict=True) if c.query != o.query
    ]
    normalized = apply_chart_defaults(labeled, model)
    topn = [
        c.id for o, c in zip(labeled.charts, normalized.charts, strict=True) if c.query != o.query
    ]

    grid = RichTable(show_lines=False)
    grid.add_column("#")
    grid.add_column("viz")
    grid.add_column("title")
    grid.add_column("measure × dimension")
    for c in normalized.charts:
        q = c.query
        measures = ", ".join(f"{m.agg.value}({m.column})" for m in q.measures)
        dims = ", ".join(q.group_columns()) or "—"
        grid.add_row(c.id, c.viz.value, c.title, f"{measures}  ×  {dims}")
    console.print(grid)

    if relabeled:
        console.print(
            f"[dim]normalize · label-join (B3): raw FK ids swapped for names via LEFT JOIN "
            f"in {relabeled}[/dim]"
        )
    if topn:
        console.print(
            f"[dim]normalize · default top-N (B1) applied to categorical charts {topn}[/dim]"
        )

    errors = validate_spec(normalized, model)
    verdict = "[green]validate_spec: 0 errors[/green]" if not errors else f"[red]{errors}[/red]"
    console.print(verdict)
    return normalized


def show_compiled_sql(spec: DashboardSpec) -> None:
    _act(3, "Compiled SQL — deterministic IR → SQL (the LLM never writes SQL)")
    # one KPI and one joined breakdown make the point: aggregation + the id→name LEFT JOIN
    picks = []
    kpi = next((c for c in spec.charts if c.viz == Viz.BIG_NUMBER), None)
    joined = next((c for c in spec.charts if c.query.joins), None)
    for c in (kpi, joined):
        if c is not None and c not in picks:
            picks.append(c)
    for c in picks:
        sql = generate_chart_sql(c.query)
        console.print(Panel(Syntax(sql, "sql", word_wrap=True), title=f"{c.id} · {c.title}"))


def _off_key_dimension(table) -> Column | None:
    """A dimension column that is NOT in the sorting key — filtering on it is the access
    path the mart isn't designed for (drives the DM_CHANGE_REQUEST verdict)."""
    sk = set(table.physical.sorting_key or []) if table.physical else set()
    from auto_bi.semantic.model import ColumnRole

    return next(
        (
            c
            for c in table.columns
            if c.role == ColumnRole.DIMENSION and c.name not in sk and not c.fk
        ),
        None,
    )


def show_advisor(model: SemanticModel, table_name: str) -> None:
    _act(4, "Feasibility Advisor — engine-aware verdict (the differentiator)")
    table = model.table(table_name)
    off_key = _off_key_dimension(table) if table else None
    if off_key is None:
        console.print("[dim](no off-sorting-key dimension in this model; skipping)[/dim]")
        return

    # a realistic request the overview can't serve: "revenue over time for one <off_key>"
    query = ChartQuery(
        table=table_name,
        measures=[
            Measure(
                column=next(c.name for c in table.columns if c.role.value == "measure"),
                agg=Aggregation.SUM,
            )
        ],
        dimensions=[next(c.name for c in table.columns if c.role.value == "time")],
        filters=[QueryFilter(column=off_key.name, op=FilterOp.EQ, value="42")],
    )
    console.print(
        f"[dim]request: sum over time, filtered to a single {off_key.name!r} "
        f"(not in the sorting key {table.physical.sorting_key})[/dim]"
    )
    findings = Advisor(model).review_chart(
        ChartSpec(id="ask", title="ad-hoc", viz=Viz.LINE, query=query)
    )
    if not findings:
        console.print("[green]no feasibility concerns[/green]")
        return
    for f in findings:
        colour = {"dm_change_request": "red", "spec_adjustment": "yellow"}.get(
            f.verdict_class.value, "white"
        )
        head = f"[{colour}]{f.verdict_class.value}[/{colour}] · {f.severity.value} · {f.rule}"
        console.print(
            Panel(
                f"{f.title}\n\n[dim]suggestion:[/dim] " + "; ".join(f.suggestions),
                title=head,
                border_style=colour,
            )
        )
    console.print(
        "[dim]The verdict is decided by code (rules over the mart's physical metadata); the LLM "
        "only narrates it. No competitor surfaces a DM-change request with this evidence.[/dim]"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto_BI golden-path demo (offline, no stand).")
    parser.add_argument("--model-path", default="semantic/model.yaml")
    parser.add_argument("--table", default="dm.sales_daily")
    args = parser.parse_args()

    console.print(
        Panel.fit(
            "[bold]Auto_BI — golden path[/bold]\n"
            "text/fields/auto → grounded IR → validated SQL → feasibility advisor → BI dashboard\n"
            "[dim]runs everything but the final live BUILD, offline from a clean clone[/dim]"
        )
    )
    model = SemanticModel.load(args.model_path)
    show_model(model)
    spec = show_auto_overview(model, args.table)
    show_compiled_sql(spec)
    show_advisor(model, args.table)

    _act(5, "Live build (the only step that needs the stand)")
    console.print(
        "Run against a running Superset/DataLens stand to assemble the real dashboard:\n"
        f"  [bold]auto_bi build --auto {args.table} --target superset[/bold]\n"
        "[dim]It re-runs the steps above, then EXPLAIN+LIMIT-checks each SQL on the live DWH and "
        "builds the dashboard via the BI's API. See docs/USER_GUIDE.md.[/dim]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
