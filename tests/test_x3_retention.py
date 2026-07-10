"""X-3 «покажи retention словами, без имени витрины» (deterministic scaffolding).

The live-LLM half of X-3 (does the model actually answer with a cohort dashboard)
is a stand e2e; these tests pin the deterministic contract around it: the request
vocabulary reaches the prompts (synonyms in grounding/propose context), the propose
rules carry the cohort pattern, and the canonical cohort spec the pattern asks for
is valid IR that compiles to SQL.
"""

from auto_bi.agent.grounding import build_grounding_prompt
from auto_bi.agent.propose import SPEC_RULES, build_propose_prompt
from auto_bi.agent.sqlgen import generate_chart_sql
from auto_bi.ir.spec import (
    ChartQuery,
    ChartSpec,
    DashboardSpec,
    FilterOp,
    LayoutHint,
    Measure,
    QueryFilter,
    Viz,
)
from auto_bi.ir.validate import validate_spec
from auto_bi.semantic.model import Aggregation, SemanticModel

REQUEST = "Покажи удержание клиентов"  # no mart name anywhere
MODEL = SemanticModel.load("semantic/model_stand.yaml")

CUSTOMERS = Measure(column="customers", agg=Aggregation.SUM, label="Клиентов")


def _canonical_cohort_spec() -> DashboardSpec:
    """The dashboard shape SPEC_RULES rule 12 asks the LLM for."""
    return DashboardSpec(
        title="Когортный ретеншен клиентов",
        charts=[
            ChartSpec(
                id="kpi_base",
                title="Всего клиентов",
                viz=Viz.BIG_NUMBER,
                query=ChartQuery(
                    table="dm.cohort_retention",
                    measures=[CUSTOMERS],
                    filters=[QueryFilter(column="months_since", op=FilterOp.EQ, value=0)],
                ),
                layout_hint=LayoutHint(w=4, h=2, row=0),
            ),
            ChartSpec(
                id="triangle",
                title="Когортный треугольник",
                viz=Viz.HEATMAP,
                query=ChartQuery(
                    table="dm.cohort_retention",
                    dimensions=["cohort_month", "months_since"],
                    measures=[CUSTOMERS],
                ),
                layout_hint=LayoutHint(w=12, h=5, row=1),
            ),
            ChartSpec(
                id="m1_by_cohort",
                title="Удержание 1-го месяца по когортам",
                viz=Viz.BAR,
                query=ChartQuery(
                    table="dm.cohort_retention",
                    dimensions=["cohort_month"],
                    measures=[CUSTOMERS],
                    filters=[QueryFilter(column="months_since", op=FilterOp.EQ, value=1)],
                ),
                layout_hint=LayoutHint(w=12, h=4, row=2),
            ),
        ],
    )


def test_grounding_prompt_carries_retention_vocabulary() -> None:
    # the words of the request map to the mart ONLY through synonyms; they must be
    # in the grounding prompt for the LLM to cite the mart as a candidate
    prompt = build_grounding_prompt(REQUEST, MODEL)
    assert "dm.cohort_retention" in prompt
    assert "удержание" in prompt  # rendered synonym, not a physical name


def test_propose_prompt_carries_mart_and_pattern() -> None:
    prompt = build_propose_prompt(REQUEST, MODEL)
    assert "dm.cohort_retention" in prompt  # selection kept the mart for this request
    assert "Когортный анализ" in prompt  # rule 12 present


def test_cohort_pattern_rule_pinned() -> None:
    # the canonical set the rule asks for — drift here silently degrades X-3
    for anchor in ("heatmap-треугольник", "фильтр <период>=1", "фильтр <период>=0"):
        assert anchor in SPEC_RULES


def test_canonical_cohort_spec_is_valid_ir_and_compiles() -> None:
    spec = _canonical_cohort_spec()
    assert validate_spec(spec, MODEL) == []
    for chart in spec.charts:
        sql = generate_chart_sql(chart.query)
        assert '"dm"."cohort_retention"' in sql  # quoted identifiers in the rendered SQL
    triangle_sql = generate_chart_sql(spec.charts[1].query)
    assert "GROUP BY" in triangle_sql
