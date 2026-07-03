"""propose_spec: prompt content + model-level repair loop on a fake LLM."""

import copy

import pytest

from auto_bi.agent.propose import (
    MAX_VALIDATION_ROUNDS,
    SpecValidationError,
    build_propose_prompt,
    patch_spec,
    propose_spec,
)
from auto_bi.ir.spec import DashboardSpec
from auto_bi.semantic.model import Column, ColumnRole, SemanticModel, Table

GOOD_SPEC = {
    "title": "Продажи",
    "charts": [
        {
            "id": "c1",
            "title": "Выручка по дням",
            "viz": "line",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["date"],
                "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
            },
        }
    ],
}

BAD_SPEC = {
    "title": "Продажи",
    "charts": [
        {
            "id": "c1",
            "title": "Выручка по дням",
            "viz": "line",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["date"],
                "measures": [{"column": "вымышленная_колонка", "agg": "sum"}],
            },
        }
    ],
}


class FakeLLM:
    """Returns queued specs; records prompts (LLMClient protocol, no transport)."""

    def __init__(self, specs: list[dict]) -> None:
        self._queue = list(specs)
        self.prompts: list[str] = []

    def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
        self.prompts.append(prompt)
        return schema.model_validate(self._queue.pop(0))


def test_prompt_contains_model_request_and_schema(demo_model) -> None:
    prompt = build_propose_prompt("выручка по дням за квартал", demo_model)
    assert "выручка по дням за квартал" in prompt
    assert "dm.sales_daily" in prompt
    assert "Выручка, руб" in prompt  # column descriptions reach the LLM
    assert "big_number" in prompt  # viz rules
    assert '"DashboardSpec"' in prompt or "properties" in prompt  # JSON schema embedded


def test_spec_rules_document_the_analytical_core() -> None:
    # drift guard (S01/F1): every analytical primitive of the IR must be explained to
    # the LLM — a new MeasureTransform/TimeGrain member fails here until SPEC_RULES
    # teaches it (text-first is the promise, not just the schema)
    from auto_bi.agent.propose import SPEC_RULES
    from auto_bi.ir.spec import MeasureTransform, TimeGrain

    for t in MeasureTransform:
        assert t.value in SPEC_RULES, f"transform {t.value!r} is not documented in SPEC_RULES"
    for g in TimeGrain:
        if g is TimeGrain.DAY:  # day = raw axis, deliberately not suggested
            continue
        assert g.value in SPEC_RULES, f"time grain {g.value!r} is not documented in SPEC_RULES"
    for field in ("denominator", "time_grain", "lag_periods", "bins", "histogram"):
        assert field in SPEC_RULES, f"IR field {field!r} is not documented in SPEC_RULES"


def test_prompt_renders_core_examples(demo_model) -> None:
    # the JSON examples survive .format() (escaped braces render to real JSON)
    prompt = build_propose_prompt("средний чек по дням", demo_model)
    assert '"denominator": {"column": "orders", "agg": "sum"}' in prompt
    assert '"transform": "yoy_pct"' in prompt
    assert '"bins": 20' in prompt


def test_happy_path_first_try(demo_model) -> None:
    llm = FakeLLM([GOOD_SPEC])
    spec = propose_spec(llm, demo_model, "выручка по дням")
    assert isinstance(spec, DashboardSpec)
    assert len(llm.prompts) == 1


def test_repair_loop_feeds_errors_back(demo_model) -> None:
    llm = FakeLLM([BAD_SPEC, GOOD_SPEC])
    spec = propose_spec(llm, demo_model, "выручка по дням")
    assert spec.charts[0].query.measures[0].column == "revenue"
    assert len(llm.prompts) == 2
    assert "вымышленная_колонка" in llm.prompts[1]  # errors quoted verbatim
    assert "не прошёл валидацию" in llm.prompts[1]
    assert "JSON Schema" in llm.prompts[1]  # repair sees the schema, not just errors


def _bad_spec_variant(col: str) -> dict:
    bad = copy.deepcopy(BAD_SPEC)
    bad["charts"][0]["query"]["measures"][0]["column"] = col
    return bad


def test_gives_up_after_max_rounds(demo_model) -> None:
    # distinct invalid specs each round so the unchanged-spec early-abort doesn't fire
    specs = [_bad_spec_variant(f"вымышленная_{i}") for i in range(1 + MAX_VALIDATION_ROUNDS)]
    llm = FakeLLM(specs)
    with pytest.raises(SpecValidationError) as exc_info:
        propose_spec(llm, demo_model, "выручка по дням")
    assert any("вымышленная_3" in e for e in exc_info.value.errors)
    assert len(llm.prompts) == 1 + MAX_VALIDATION_ROUNDS


def test_aborts_when_spec_unchanged(demo_model) -> None:
    # identical invalid spec twice -> no point repairing again, stop early (F7)
    llm = FakeLLM([BAD_SPEC, BAD_SPEC, BAD_SPEC, BAD_SPEC])
    with pytest.raises(SpecValidationError):
        propose_spec(llm, demo_model, "выручка по дням")
    assert len(llm.prompts) == 2  # first try + one repair that came back identical


def test_patch_keeps_spec_tables_on_huge_model(demo_model) -> None:
    # a short edit ("переименуй") has zero lexical overlap with dm.sales_daily: on a
    # DM that does not fit the budget, selection must still keep the spec's tables,
    # or the previously valid spec fails validation against the sub-model
    noise_tables = [
        Table(
            name=f"dm.noise_{i}",
            description=f"Переименованные витрины раздела {i} про дашборды и заголовки",
            columns=[
                Column(
                    name=f"col_{j}",
                    type="String",
                    role=ColumnRole.DIMENSION,
                    description=f"Колонка про переименование и заголовок номер {j}",
                    top_values=[f"переименование_{j}_{k}" for k in range(10)],
                )
                for j in range(40)
            ],
        )
        for i in range(120)
    ]
    huge = SemanticModel(tables=[*demo_model.tables, *noise_tables], joins=demo_model.joins)
    spec = DashboardSpec.model_validate(GOOD_SPEC)
    llm = FakeLLM([{**GOOD_SPEC, "title": "Продажи (новое имя)"}])
    patched = patch_spec(llm, huge, spec, "переименуй дашборд")
    assert patched.title == "Продажи (новое имя)"
    assert len(llm.prompts) == 1  # no repair round: the spec's table was in the sub-model
    assert "dm.sales_daily" in llm.prompts[0]


def test_send_samples_false_strips_values(demo_model) -> None:
    with_values = build_propose_prompt("x", demo_model, include_samples=True)
    without_values = build_propose_prompt("x", demo_model, include_samples=False)
    assert "Москва" in with_values  # top_values reach the LLM by default
    assert "Москва" not in without_values  # suppressed for sensitive DMs
    assert "dm.sales_daily" in without_values  # schema/metadata still sent
