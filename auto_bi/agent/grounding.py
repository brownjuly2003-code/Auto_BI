"""GROUNDING (task 1.4): request -> grounding report against the semantic model.

The report is the ONLY source of clarifying questions (invariant 4): CLARIFY
questions are generated deterministically from `ambiguous`/`unmatched` entries,
so a clean report mechanically guarantees zero questions.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from pydantic import BaseModel, Field

from auto_bi.llm.base import LLMClient
from auto_bi.llm.policy import reasoning_for
from auto_bi.semantic.model import SemanticModel
from auto_bi.semantic.render import render_model
from auto_bi.semantic.select import PROMPT_CHAR_BUDGET, select_context

MAX_CLARIFY_QUESTIONS = 3


class EntityMatch(BaseModel):
    phrase: str  # fragment of the user request, verbatim
    candidates: list[str] = Field(default_factory=list)  # "dm.table.column" / "dm.table"
    note: str = ""


class GroundingReport(BaseModel):
    tables: list[str] = Field(default_factory=list)  # tables the dashboard will need
    matched: list[EntityMatch] = Field(default_factory=list)  # exactly one candidate each
    ambiguous: list[EntityMatch] = Field(default_factory=list)  # >=2 real candidates
    unmatched: list[str] = Field(default_factory=list)  # request entities absent from the DM

    def is_clear(self) -> bool:
        return not self.ambiguous and not self.unmatched


GROUNDING_PROMPT = """Ты сопоставляешь запрос пользователя с семантической моделью витрин данных.

Семантическая модель:

{model_text}

Запрос пользователя: {request}

Верни grounding report:
- tables — таблицы модели, которые понадобятся дашборду (полные имена вида "dm.sales_daily");
- matched — сущности запроса, найденные в модели ОДНОЗНАЧНО: phrase (фрагмент запроса),
  candidates (ровно один элемент "schema.table.column" или "schema.table"), note;
- ambiguous — сущности, у которых в модели НЕСКОЛЬКО реальных кандидатов (все кандидаты
  в candidates); только настоящая неоднозначность, ничего не выдумывай;
- unmatched — сущности запроса, которых в модели нет (фразы из запроса как есть).

Правила:
1. candidates — только имена из модели выше.
2. Слова о форме подачи и виде анализа («график», «топ-10», «по дням», «по месяцам»,
   «дашборд», «год к году», «месяц к месяцу», «накопительно», «нарастающим итогом»,
   «Парето», «ABC», «доля от общего», «распределение») — НЕ сущности данных:
   их не относить ни к ambiguous, ни к unmatched.
3. Если запрос однозначен — ambiguous и unmatched оставь пустыми. Лишние уточнения вредны.
4. Колонки с одинаковым смыслом в разных таблицах («название» есть и в товарах,
   и в магазинах) — это ambiguous ТОЛЬКО когда остальной запрос таблицу не определяет:
   тогда перечисли кандидатов из всех таблиц и не выбирай сам. Если сущность естественно
   ложится в ту же таблицу, где остальные поля запроса (мера, время) — например,
   «по магазинам» при мере из таблицы продаж, где есть store_id, — клади её в matched:
   лишний вопрос хуже разумного выбора по контексту.
5. Производные метрики-отношения («средний чек» = выручка/заказы, «маржа» =
   прибыль/выручка): дашборд умеет делить одну меру на другую. Если ОБЕ составляющие
   есть мерами в модели — это matched: phrase = метрика, candidates = обе колонки-меры
   (для такой пары два кандидата допустимы), в note — разложение («revenue / orders»).
   Если хотя бы одной составляющей в модели нет («конверсия сайта» без визитов/сессий) —
   unmatched: не подменяй похожей мерой. (Доли в pie-чарте — форма подачи, НЕ
   производная метрика.)

Ответ — ТОЛЬКО JSON-объект GroundingReport в блоке ```json```, без пояснений.

JSON Schema ответа:
{schema}"""


def build_grounding_prompt(
    request: str,
    model: SemanticModel,
    *,
    include_samples: bool = True,
    pinned: Iterable[str] = (),
) -> str:
    schema = json.dumps(GroundingReport.model_json_schema(), ensure_ascii=False)
    fixed = len(GROUNDING_PROMPT.format(model_text="", request=request, schema=schema))
    sub = select_context(
        model,
        request,
        budget_chars=PROMPT_CHAR_BUDGET - fixed,
        include_samples=include_samples,
        pinned=pinned,
    )
    return GROUNDING_PROMPT.format(
        model_text=render_model(sub, include_samples=include_samples),
        request=request,
        schema=schema,
    )


def ground(
    llm: LLMClient,
    model: SemanticModel,
    request: str,
    *,
    session_id: str | None = None,
    include_samples: bool = True,
    pinned: Iterable[str] = (),
) -> GroundingReport:
    prompt = build_grounding_prompt(request, model, include_samples=include_samples, pinned=pinned)
    report = llm.complete(
        prompt,
        GroundingReport,
        reasoning=reasoning_for("grounding"),
        session_id=session_id,
        step="grounding",
    )
    # drop hallucinated candidates: an entry whose candidates are not in the model is
    # not a real ambiguity (invariant 4 — questions must come from grounded facts only)
    known = {t.name for t in model.tables} | {
        f"{t.name}.{c.name}" for t in model.tables for c in t.columns
    }
    for entry in list(report.ambiguous):
        entry.candidates = [c for c in entry.candidates if c in known]
        if len(entry.candidates) < 2:  # not ambiguous once fakes are gone
            report.ambiguous.remove(entry)
            if entry.candidates:
                report.matched.append(entry)
            else:
                report.unmatched.append(entry.phrase)
    return report


def clarify_questions(report: GroundingReport) -> list[str]:
    """Deterministic question generation: only from the report, max 3 (invariant 4)."""
    questions: list[str] = []
    for entry in report.ambiguous:
        questions.append(
            f"Под «{entry.phrase}» подходит несколько полей: "
            f"{', '.join(entry.candidates)}. Какое использовать?"
        )
    for phrase in report.unmatched:
        questions.append(
            f"«{phrase}» не нашлось в витринах. Уточните, что имеется в виду, "
            f"или скажите строить без этого."
        )
    return questions[:MAX_CLARIFY_QUESTIONS]
