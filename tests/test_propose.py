"""propose_spec: prompt content + model-level repair loop on a fake LLM."""

import copy

import pytest

from auto_bi.agent.propose import (
    MAX_VALIDATION_ROUNDS,
    SpecValidationError,
    build_propose_prompt,
    propose_spec,
)
from auto_bi.ir.spec import DashboardSpec

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

    def complete(self, prompt, schema, *, reasoning=False, session_id=None):
        self.prompts.append(prompt)
        return schema.model_validate(self._queue.pop(0))


def test_prompt_contains_model_request_and_schema(demo_model) -> None:
    prompt = build_propose_prompt("выручка по дням за квартал", demo_model)
    assert "выручка по дням за квартал" in prompt
    assert "dm.sales_daily" in prompt
    assert "Выручка, руб" in prompt  # column descriptions reach the LLM
    assert "big_number" in prompt  # viz rules
    assert '"DashboardSpec"' in prompt or "properties" in prompt  # JSON schema embedded


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


def test_send_samples_false_strips_values(demo_model) -> None:
    with_values = build_propose_prompt("x", demo_model, include_samples=True)
    without_values = build_propose_prompt("x", demo_model, include_samples=False)
    assert "Москва" in with_values  # top_values reach the LLM by default
    assert "Москва" not in without_values  # suppressed for sensitive DMs
    assert "dm.sales_daily" in without_values  # schema/metadata still sent
