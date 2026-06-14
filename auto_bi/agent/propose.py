"""PROPOSE_SPEC (Phase 0 happy path): request + semantic model -> validated DashboardSpec.

Two-level repair: schema-level lives in LLMClient.complete; model-level (unknown
fields etc., invariant 2) is the loop here — validation errors go back to the LLM
verbatim, max 3 rounds, never fixed silently.
"""

import json
import logging

from auto_bi.ir.spec import DashboardSpec
from auto_bi.ir.validate import validate_spec
from auto_bi.llm.base import LLMClient
from auto_bi.llm.policy import reasoning_for
from auto_bi.semantic.model import SemanticModel
from auto_bi.semantic.render import render_model
from auto_bi.semantic.select import PROMPT_CHAR_BUDGET, select_context

logger = logging.getLogger(__name__)

MAX_VALIDATION_ROUNDS = 3
FEEDBACK_MARGIN = 2_000  # validation-feedback rounds add error lines to the prompt

SPEC_RULES = """Правила:
1. Используй ТОЛЬКО таблицы и колонки из семантической модели выше. Ничего не выдумывай.
   Базовая таблица чарта — query.table; меры (measures) — только её колонки.
   Измерение из СМЕЖНОЙ таблицы допустимо, если в разделе «Джойны» модели есть связь:
   укажи его ПОЛНЫМ именем ("dm.stores.city") и добавь соответствующий джойн в
   query.joins: {{"table": "dm.stores", "on_left": "dm.sales_daily.store_id",
   "on_right": "dm.stores.id"}} — ровно та пара колонок, что указана в «Джойны»
   (выдуманные условия будут отклонены). Без подходящей связи в модели — замени на
   ближайшую колонку самой таблицы.
2. Доступные viz-типы и их роли (заполняй ТОЛЬКО роли, перечисленные для типа):
   - big_number — одна мера, без измерений (итоговый KPI);
   - line / area — тренд по времени: dimensions=[<time>, ...]; series — необязательная разбивка;
   - bar / stacked_bar — сравнение по категории: dimensions=[<категория>, ...];
     series — необязательная разбивка/стек;
   - pie — доли: ровно один dimensions и одна мера;
   - table — таблица: dimensions + measures (несколько допустимо);
   - pivot — сводная: rows=[...] (обязательно), columns=[...] (необязательно),
     measures; dimensions НЕ заполнять;
   - heatmap — ровно два dimensions (x,y) и одна мера.
3. measures — только колонки с ролью measure; dimensions/series/rows/columns — колонки с ролью
   dimension или time. Не клади одну и ту же колонку в несколько ролей.
   Имена колонок везде — БЕЗ префикса таблицы: "revenue", НЕ "dm.sales_daily.revenue"
   (полное имя только в query.table; исключение — dashboard-фильтры верхнего уровня,
   там колонка полная: "dm.sales_daily.date").
4. Для line/area первым в dimensions ставь колонку с ролью time.
5. Для bar/stacked_bar/pie/table ограничивай выдачу: order_by по мере desc + разумный limit (10–50).
6. layout_hint: сетка 12 колонок; big_number — w=4 h=2; остальные — w=6..12 h=4; row нумеруй с 0.
7. Заголовки дашборда и чартов — по-русски, кратко и по делу."""

PROPOSE_SPEC_PROMPT = (
    """Ты — аналитик, который проектирует BI-дашборды по витринам данных.

Семантическая модель доступных витрин:

{model_text}

Запрос пользователя: {request}

Спроектируй дашборд из 2–4 чартов под этот запрос.

"""
    + SPEC_RULES
    + """

Ответ — ТОЛЬКО JSON-объект DashboardSpec в блоке ```json```, без пояснений.

JSON Schema ответа:
{schema}"""
)

PATCH_SPEC_PROMPT = (
    """Ты дорабатываешь существующий BI-дашборд (DashboardSpec) по правке пользователя.

Семантическая модель доступных витрин:

{model_text}

Текущий DashboardSpec:
```json
{spec}
```

Правка пользователя: {request}

Верни ПОЛНЫЙ обновлённый DashboardSpec: внеси ровно запрошенные изменения, остальное
сохрани как есть (id нетронутых чартов не меняй).

"""
    + SPEC_RULES
    + """

Ответ — ТОЛЬКО JSON-объект DashboardSpec в блоке ```json```, без пояснений.

JSON Schema ответа:
{schema}"""
)

VALIDATION_FEEDBACK_PROMPT = """Твой DashboardSpec не прошёл валидацию по семантической модели.

Ошибки:
{errors}

Семантическая модель (используй только её поля):

{model_text}

Исходный запрос пользователя: {request}

Верни ИСПРАВЛЕННЫЙ DashboardSpec. Только JSON в блоке ```json```, без пояснений.

JSON Schema ответа:
{schema}"""


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _select_for_prompt(
    request: str,
    model: SemanticModel,
    *,
    include_samples: bool,
    fixed_chars: int | None = None,
    scoring_text: str | None = None,
    pinned: set[str] | None = None,
) -> SemanticModel:
    """Context selection (task 1.5): fit the model into the 40k prompt limit.

    The budget is whatever the fixed prompt parts (template, request, JSON Schema —
    and for patch_spec the current spec JSON) leave, minus a margin for
    validation-feedback rounds. Idempotent: a sub-model that already fits comes back
    unchanged.
    """
    if fixed_chars is None:
        schema = json.dumps(DashboardSpec.model_json_schema(), ensure_ascii=False)
        fixed_chars = len(PROPOSE_SPEC_PROMPT.format(model_text="", request=request, schema=schema))
    budget = PROMPT_CHAR_BUDGET - fixed_chars - FEEDBACK_MARGIN
    return select_context(
        model,
        scoring_text or request,
        budget_chars=budget,
        include_samples=include_samples,
        pinned=pinned or (),
    )


def build_propose_prompt(
    request: str,
    model: SemanticModel,
    *,
    include_samples: bool = True,
    pinned: set[str] | None = None,
) -> str:
    model = _select_for_prompt(request, model, include_samples=include_samples, pinned=pinned)
    return PROPOSE_SPEC_PROMPT.format(
        model_text=render_model(model, include_samples=include_samples),
        request=request,
        schema=json.dumps(DashboardSpec.model_json_schema(), ensure_ascii=False),
    )


def propose_spec(
    llm: LLMClient,
    model: SemanticModel,
    request: str,
    *,
    session_id: str | None = None,
    include_samples: bool = True,
    pinned: set[str] | None = None,
) -> DashboardSpec:
    # select once and use the SAME sub-model for prompting, validation and repair:
    # the LLM may only reference what it was actually shown
    model = _select_for_prompt(request, model, include_samples=include_samples, pinned=pinned)
    prompt = build_propose_prompt(request, model, include_samples=include_samples, pinned=pinned)
    return _complete_validated(
        llm,
        model,
        prompt,
        request,
        step="propose_spec",
        session_id=session_id,
        include_samples=include_samples,
    )


def patch_spec(
    llm: LLMClient,
    model: SemanticModel,
    spec: DashboardSpec,
    edit_request: str,
    *,
    session_id: str | None = None,
    include_samples: bool = True,
) -> DashboardSpec:
    """Word edits in APPROVE (task 1.4): current spec + правка -> new validated spec.

    Selection must not lose the tables/columns the current spec is built on: a short
    edit ("переименуй дашборд") carries no lexical signal about untouched charts, so
    the spec's tables are pinned and its fields join the scoring text — otherwise on
    a big DM validation would reject a previously valid spec and the repair loop
    would push the LLM to re-seat charts onto other tables.
    """
    spec_json = spec.model_dump_json()
    schema = json.dumps(DashboardSpec.model_json_schema(), ensure_ascii=False)
    fixed = len(
        PATCH_SPEC_PROMPT.format(model_text="", spec=spec_json, request=edit_request, schema=schema)
    )
    model = _select_for_prompt(
        edit_request,
        model,
        include_samples=include_samples,
        fixed_chars=fixed,
        scoring_text=f"{edit_request} {_spec_terms(spec)}",
        pinned={chart.query.table for chart in spec.charts},
    )
    prompt = PATCH_SPEC_PROMPT.format(
        model_text=render_model(model, include_samples=include_samples),
        spec=spec_json,
        request=edit_request,
        schema=schema,
    )
    return _complete_validated(
        llm,
        model,
        prompt,
        edit_request,
        step="patch_spec",
        session_id=session_id,
        include_samples=include_samples,
    )


def _spec_terms(spec: DashboardSpec) -> str:
    """Everything the spec references, as scoring text for context selection."""
    terms: list[str] = [spec.title]
    for f in spec.filters:
        terms.append(f.column)
    for chart in spec.charts:
        q = chart.query
        terms.extend((chart.title, q.table))
        terms.extend(q.group_columns())
        terms.extend(m.column for m in q.measures)
        terms.extend(qf.column for qf in q.filters)
    return " ".join(terms)


def _complete_validated(
    llm: LLMClient,
    model: SemanticModel,
    prompt: str,
    request: str,
    *,
    step: str,
    session_id: str | None,
    include_samples: bool,
) -> DashboardSpec:
    """Shared model-level repair loop (invariant 2): errors go back verbatim, max 3."""
    previous_spec: DashboardSpec | None = None
    reasoning = reasoning_for(step)
    for round_no in range(1 + MAX_VALIDATION_ROUNDS):
        spec = llm.complete(
            prompt, DashboardSpec, reasoning=reasoning, session_id=session_id, step=step
        )
        errors = validate_spec(spec, model)
        if not errors:
            return spec
        logger.warning("spec failed model validation (round %d): %s", round_no + 1, errors)
        if spec == previous_spec:  # model is stuck repeating the same invalid spec
            logger.warning("spec unchanged after validation feedback; aborting repair loop")
            break
        previous_spec = spec
        prompt = VALIDATION_FEEDBACK_PROMPT.format(
            errors="\n".join(f"- {e}" for e in errors),
            model_text=render_model(model, include_samples=include_samples),
            request=request,
            schema=json.dumps(DashboardSpec.model_json_schema(), ensure_ascii=False),
        )
    raise SpecValidationError(errors)
