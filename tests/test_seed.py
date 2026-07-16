"""Fields-first seed (task 2.3): validation, rendering, layout analysis, machine flow."""

import pytest

from auto_bi.agent.machine import AgentPhase, AgentSession
from auto_bi.agent.seed import (
    FieldsSeed,
    SeedGroup,
    render_seed_request,
    seed_analysis,
    seed_tables,
    validate_seed,
)
from auto_bi.ir.spec import DashboardSpec
from tests.test_machine import CLEAR_REPORT, ScriptedLLM
from tests.test_propose import GOOD_SPEC


def make_seed(**kwargs) -> FieldsSeed:
    groups = kwargs.pop(
        "groups",
        [
            SeedGroup(
                label="Выручка по дням",
                fields=["dm.sales_daily.date", "dm.sales_daily.revenue"],
            ),
            SeedGroup(fields=["dm.stores.city", "dm.sales_daily.orders"]),
        ],
    )
    return FieldsSeed(groups=groups, **kwargs)


# --- validation ------------------------------------------------------------------


def test_valid_seed_passes(demo_model) -> None:
    assert validate_seed(make_seed(), demo_model) == []


def test_unknown_field_reported_with_group(demo_model) -> None:
    seed = make_seed(groups=[SeedGroup(fields=["dm.sales_daily.margin"])])
    errors = validate_seed(seed, demo_model)
    assert len(errors) == 1
    assert "dm.sales_daily.margin" in errors[0]
    assert "группа 1" in errors[0]


def test_seed_requires_groups_and_fields() -> None:
    with pytest.raises(ValueError):
        FieldsSeed(groups=[])
    with pytest.raises(ValueError):
        SeedGroup(fields=[])


def test_seed_tables_collects_unique(demo_model) -> None:
    assert seed_tables(make_seed()) == {"dm.sales_daily", "dm.stores"}


# --- rendering -------------------------------------------------------------------


def test_render_carries_groups_roles_and_comment(demo_model) -> None:
    text = render_seed_request(make_seed(comment="без копеек"), demo_model)
    assert "Группа 1 «Выручка по дням»" in text
    assert "dm.sales_daily.revenue (measure, agg sum)" in text
    assert "dm.sales_daily.date (time)" in text
    assert "Группа 2:" in text
    assert "Комментарий пользователя: без копеек" in text
    # instruction block: groups are drafts, the LLM still picks viz
    assert "черновик" in text


# --- layout analysis (deterministic, D5) -------------------------------------------


def test_analysis_silent_when_seed_fully_used() -> None:
    seed = FieldsSeed(groups=[SeedGroup(fields=["dm.sales_daily.date", "dm.sales_daily.revenue"])])
    assert seed_analysis(seed, DashboardSpec.model_validate(GOOD_SPEC)) == []


def test_analysis_reports_dropped_fields_and_group_count() -> None:
    spec = DashboardSpec.model_validate(GOOD_SPEC)  # 1 chart: date x revenue
    notes = seed_analysis(make_seed(), spec)
    dropped = [n for n in notes if "не вошли" in n]
    assert len(dropped) == 1
    assert "dm.stores.city" in dropped[0] and "dm.sales_daily.orders" in dropped[0]
    assert any("2 групп" in n and "1 чартов" in n for n in notes)


def test_analysis_join_field_not_false_dropped() -> None:
    """P2-2: a joined FQ dimension in the spec must match the seed field, not
    get double-qualified as base.table.dm.stores.city and reported dropped."""
    seed = FieldsSeed(
        groups=[
            SeedGroup(
                fields=[
                    "dm.sales_daily.date",
                    "dm.stores.city",
                    "dm.sales_daily.revenue",
                ]
            )
        ]
    )
    spec = DashboardSpec.model_validate(
        {
            "title": "По городам",
            "charts": [
                {
                    "id": "c1",
                    "title": "Выручка",
                    "viz": "bar",
                    "query": {
                        "table": "dm.sales_daily",
                        "dimensions": ["dm.sales_daily.date", "dm.stores.city"],
                        "measures": [{"column": "revenue", "agg": "sum"}],
                        "joins": [
                            {
                                "table": "dm.stores",
                                "on_left": "dm.sales_daily.store_id",
                                "on_right": "dm.stores.id",
                            }
                        ],
                    },
                }
            ],
        }
    )
    assert seed_analysis(seed, spec) == []


# --- machine flow ------------------------------------------------------------------


def test_start_with_seed_reaches_approve_with_analysis(demo_model) -> None:
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC])
    agent = AgentSession(demo_model, llm)
    turn = agent.start(seed=make_seed())
    assert turn.phase == AgentPhase.APPROVE
    assert turn.spec is not None
    # the grounding prompt consumed the rendered seed, not an empty request
    grounding_prompt = llm.calls[0][1]
    assert "Группа 1 «Выручка по дням»" in grounding_prompt
    # dropped seed fields surface as deterministic layout analysis: in the message
    # (CLI) and as structured notes (web UI renders them in the spec preview)
    assert "анализ раскладки" in turn.message
    assert "dm.stores.city" in turn.message
    assert any("dm.stores.city" in n for n in turn.notes)


def test_word_edit_after_seed_does_not_repeat_analysis(demo_model) -> None:
    patched = {**GOOD_SPEC, "title": "Продажи (обновлено)"}
    llm = ScriptedLLM([CLEAR_REPORT, GOOD_SPEC, patched])
    agent = AgentSession(demo_model, llm)
    agent.start(seed=make_seed())
    turn = agent.reply("убери город")
    assert turn.phase == AgentPhase.APPROVE
    assert "анализ раскладки" not in turn.message  # edits diverge from the seed on purpose


def test_start_requires_request_or_seed(demo_model) -> None:
    agent = AgentSession(demo_model, ScriptedLLM([]))
    with pytest.raises(ValueError):
        agent.start("   ")
