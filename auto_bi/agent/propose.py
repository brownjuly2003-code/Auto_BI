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
from auto_bi.semantic.model import SemanticModel
from auto_bi.semantic.render import render_model

logger = logging.getLogger(__name__)

MAX_VALIDATION_ROUNDS = 3

PROPOSE_SPEC_PROMPT = """Ты — аналитик, который проектирует BI-дашборды по витринам данных.

Семантическая модель доступных витрин:

{model_text}

Запрос пользователя: {request}

Спроектируй дашборд из 2–4 чартов под этот запрос.

Правила:
1. Используй ТОЛЬКО таблицы и колонки из семантической модели выше. Ничего не выдумывай.
2. Доступные viz-типы: big_number (одна мера, без измерений), line (тренд по времени),
   bar (сравнение по категории).
3. measures — только колонки с ролью measure; dimensions — колонки с ролью dimension или time.
4. Для line первым dimension ставь колонку с ролью time.
5. У bar-чартов ограничивай выдачу: order_by по мере desc + разумный limit (10–50).
6. layout_hint: сетка 12 колонок; big_number — w=4 h=2; line/bar — w=6..12 h=4; row нумеруй с 0.
7. Заголовки дашборда и чартов — по-русски, кратко и по делу.

Ответ — ТОЛЬКО JSON-объект DashboardSpec в блоке ```json```, без пояснений.

JSON Schema ответа:
{schema}"""

VALIDATION_FEEDBACK_PROMPT = """Твой DashboardSpec не прошёл валидацию по семантической модели.

Ошибки:
{errors}

Семантическая модель (используй только её поля):

{model_text}

Исходный запрос пользователя: {request}

Верни ИСПРАВЛЕННЫЙ DashboardSpec. Только JSON в блоке ```json```, без пояснений."""


class SpecValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def build_propose_prompt(request: str, model: SemanticModel) -> str:
    return PROPOSE_SPEC_PROMPT.format(
        model_text=render_model(model),
        request=request,
        schema=json.dumps(DashboardSpec.model_json_schema(), ensure_ascii=False),
    )


def propose_spec(
    llm: LLMClient,
    model: SemanticModel,
    request: str,
    *,
    session_id: str | None = None,
) -> DashboardSpec:
    prompt = build_propose_prompt(request, model)
    for round_no in range(1 + MAX_VALIDATION_ROUNDS):
        spec = llm.complete(prompt, DashboardSpec, reasoning=True, session_id=session_id)
        errors = validate_spec(spec, model)
        if not errors:
            return spec
        logger.warning("spec failed model validation (round %d): %s", round_no + 1, errors)
        prompt = VALIDATION_FEEDBACK_PROMPT.format(
            errors="\n".join(f"- {e}" for e in errors),
            model_text=render_model(model),
            request=request,
        )
    raise SpecValidationError(errors)
