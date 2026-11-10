"""Model-derived inputs for the Superset pure builders (DECISIONOPS_ARCHITECTURE §8.5).

`build_form_data` takes a handful of arguments that are not in the IR: the human legend
name of a measure, the temporal alias a native time filter re-scopes on, the zero-padding a
numeric heatmap axis needs. Each is a pure function of the `SemanticModel` and the chart,
and each used to live as a private method on `SupersetAdapter`.

They moved here because the release gate compiles the same dashboard without touching
Superset. §8.5 forbids a second implementation of payload logic: if the contract compiler
re-derived "which column is the time axis", the gate could pass on a payload production
would never build. One function, two callers — the adapter delegates, and
`verification/bi_contract.py` imports the same names.

Live measurement (`_ru_scale` and its KPI/axis wrappers) deliberately stays on the adapter:
it queries the stand for a measure's magnitude, so it is not a pure function of the model
and has no offline answer. The compile contract records that absence instead of guessing.
"""

from __future__ import annotations

import re

from auto_bi.ir.spec import ChartQuery, ChartSpec, Measure, Viz, column_alias, measure_alias
from auto_bi.semantic.model import ColumnRole, SemanticModel

__all__ = [
    "heatmap_y_pad",
    "human_label",
    "measure_currency",
    "metric_labels",
    "temporal_alias",
]

# a measure is money (KPI unit gets a "₽") when its model description says so — kept as
# markers, not a hard-coded currency, so a count/qty KPI never gets a spurious ruble sign.
MONEY_MARKERS = ("руб", "₽", "rub")

# a measure's human legend name is the short form of its column description: text up to the
# first of these separators ("Выручка, руб" -> "Выручка"), mirroring autospec._short.
LABEL_SEPS = (",", "(", ":", " —", " -")

#: Widest numeric heatmap axis that reads as ordinal periods (cohort months/weeks) rather
#: than ids — above it, zero-padding labels would be strictly worse to read.
HEATMAP_PAD_MAX_CARD = 100

_NUMERIC_TYPE_RE = re.compile(r"int|float|decimal|numeric|double", re.IGNORECASE)


def human_label(measure: Measure, table: str, model: SemanticModel | None) -> str | None:
    """Human display name for a measure's legend/tooltip: its explicit label, else the short
    form of the model column's description ("Выручка, руб" -> "Выручка"), else None (the
    caller then falls back to the raw alias). Autospec deliberately leaves measure.label
    empty (technical SQL alias) — this recovers the human name for display only."""
    if measure.label:
        return measure.label
    if model is None:
        return None
    tbl = model.table(table)
    col = tbl.column(measure.column.rpartition(".")[2]) if tbl else None
    desc = col.description.strip() if col and col.description else ""
    if not desc:
        return None
    for sep in LABEL_SEPS:
        idx = desc.find(sep)
        if idx > 0:
            desc = desc[:idx]
    return desc.strip() or None


def metric_labels(chart: ChartSpec, model: SemanticModel | None) -> dict[str, str]:
    """alias -> human legend name for each measure that resolves one (see `human_label`)."""
    out: dict[str, str] = {}
    for m in chart.query.measures:
        human = human_label(m, chart.query.table, model)
        if human:
            out[measure_alias(m)] = human
    return out


def measure_currency(measure: Measure, table: str, model: SemanticModel | None) -> str:
    """'₽' when the measure reads as money in the model (its column description mentions
    rubles), else '' — so a count/qty KPI ('236 млн') gets no spurious currency. No model
    (bare protocol) => no currency."""
    if model is None:
        return ""
    tbl = model.table(table)
    col = tbl.column(measure.column.rpartition(".")[2]) if tbl else None
    text = f"{col.description if col else ''} {measure.label or ''}".lower()
    return "₽" if any(m in text for m in MONEY_MARKERS) else ""


def temporal_alias(
    query: ChartQuery, model: SemanticModel | None, *, from_source: bool = False
) -> str | None:
    """Alias of the temporal column for granularity_sqla, else None.

    Prefer a TIME column in the chart's grain (line/bar x-axis). On a SOURCE dataset the
    mart still carries its TIME column even when the chart does not GROUP BY it (KPI),
    so a dashboard native time filter can re-scope the multi-row aggregate — passed to
    build_form_data as granularity_sqla. Without it the ECharts/big_number query names no
    time column and the preset period (B5) silently fails to re-scope the chart.
    """
    if model is None:
        return None
    for col in query.group_columns():
        table_name, _, name = col.rpartition(".")
        table = model.table(table_name or query.table)
        column = table.column(name) if table else None
        if column is not None and column.role == ColumnRole.TIME:
            return column_alias(col)
    if from_source:
        table = model.table(query.table)
        if table is not None:
            for column in table.columns:
                if column.role == ColumnRole.TIME:
                    return column.name
    return None


def heatmap_y_pad(chart: ChartSpec, model: SemanticModel | None) -> int | None:
    """Zero-pad width for a heatmap's numeric ordinal y-axis, else None.

    heatmap_v2 renders a numeric 0 as `<NULL>` on the axis (upstream #33105) and
    alpha-sorts numeric keys (#31318); padding the value to a fixed width fixes both.
    Applied only to a small-cardinality numeric DIMENSION (cohort periods 0..N, where
    a zero row is the norm and short labels stay short) — an id-like axis (store_id,
    cardinality in the thousands) keeps its natural labels: it has no zero row to hit
    the bug, and "0001" would be strictly worse to read. Width = digits of the largest
    expected value (cardinality - 1, ordinals are dense from 0), min 2.
    """
    if chart.viz != Viz.HEATMAP or model is None or len(chart.query.dimensions) != 2:
        return None
    y = chart.query.dimensions[1]
    table_name, _, name = y.rpartition(".")
    table = model.table(table_name or chart.query.table)
    column = table.column(name) if table else None
    if column is None or column.role != ColumnRole.DIMENSION:
        return None
    if not _NUMERIC_TYPE_RE.search(column.type):
        return None
    card = (table.physical.cardinality.get(name, 0) if table and table.physical else 0) or 0
    if not 0 < card <= HEATMAP_PAD_MAX_CARD:
        return None
    return max(2, len(str(card - 1)))
