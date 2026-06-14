"""Dev-сервер для web UI без GraceKelly/стенда: скриптованный LLM + фейковый builder.

Запуск:  .venv/Scripts/python.exe scripts/dev_ui_server.py  ->  http://127.0.0.1:8201/
Сценарий зашит: первый запрос -> 2 уточняющих вопроса, ответ -> spec из 3 чартов
с двумя вердиктами advisor; approve -> "сборка" с лог-шагами; правка словами ->
spec с новым заголовком (v2); вторая правка возвращает тот же spec -> noop-ветка.
Fields-first: режим «Полями» строит панель из MODEL
(две таблицы); поля dm.stores в spec не входят -> в превью виден детерминированный
«анализ раскладки». Нужен ТОЛЬКО для ручной/браузерной проверки фронта —
никакой бизнес-логики здесь нет.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn

from auto_bi.adapters.base import DashboardRef
from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.api import create_app
from auto_bi.semantic.model import Column, ColumnRole, SemanticModel, Table
from auto_bi.store import Store

MODEL = SemanticModel(
    tables=[
        Table(
            name="dm.sales_daily",
            description="Дневные продажи",
            columns=[
                Column(name="date", type="Date", role=ColumnRole.TIME),
                Column(name="store_id", type="UInt32", role=ColumnRole.DIMENSION),
                Column(name="revenue", type="Decimal(18,2)", role=ColumnRole.MEASURE),
                Column(name="orders", type="UInt32", role=ColumnRole.MEASURE),
            ],
        ),
        Table(
            name="dm.stores",
            description="Справочник магазинов",
            columns=[
                Column(name="name", type="String", role=ColumnRole.DIMENSION),
                Column(name="city", type="LowCardinality(String)", role=ColumnRole.DIMENSION),
            ],
        ),
    ]
)

SPEC = {
    "title": "Обзор продаж",
    "filters": [{"column": "dm.sales_daily.date", "type": "time_range", "default": "last 90 days"}],
    "charts": [
        {
            "id": "c1",
            "title": "Выручка, итого",
            "viz": "big_number",
            "query": {
                "table": "dm.sales_daily",
                "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
            },
        },
        {
            "id": "c2",
            "title": "Выручка по дням",
            "viz": "line",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["date"],
                "measures": [{"column": "revenue", "agg": "sum", "label": "Выручка"}],
            },
        },
        {
            "id": "c3",
            "title": "Топ магазинов",
            "viz": "bar",
            "query": {
                "table": "dm.sales_daily",
                "dimensions": ["store_id"],
                "measures": [{"column": "orders", "agg": "sum", "label": "Заказы"}],
                "order_by": [{"by": "Заказы", "dir": "desc"}],
                "limit": 15,
            },
        },
    ],
}

AMBIGUOUS = {
    "tables": ["dm.sales_daily"],
    "matched": [],
    "ambiguous": [
        {"phrase": "продажи", "candidates": ["dm.sales_daily.revenue", "dm.sales_daily.orders"]}
    ],
    "unmatched": ["маржа"],
}
CLEAR = {
    "tables": ["dm.sales_daily"],
    "matched": [{"phrase": "выручка", "candidates": ["dm.sales_daily.revenue"]}],
    "ambiguous": [],
    "unmatched": [],
}


class DevLLM:
    """Отвечает по имени запрошенной схемы; первый grounding — с вопросами."""

    def __init__(self) -> None:
        self.grounding_calls = 0
        self.spec_calls = 0

    def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
        time.sleep(0.4)  # чтобы «агент думает» был виден
        name = schema.__name__
        if name == "GroundingReport":
            self.grounding_calls += 1
            return schema.model_validate(AMBIGUOUS if self.grounding_calls == 1 else CLEAR)
        if name == "DashboardSpec":
            self.spec_calls += 1
            spec = dict(SPEC)
            if self.spec_calls > 1:
                # title капится на v2: вторая и последующие правки возвращают тот же
                # spec — браузерная проверка noop-ветки («правка не изменила спецификацию»)
                spec = {**SPEC, "title": f"Обзор продаж · v{min(self.spec_calls, 2)}"}
            return schema.model_validate(spec)
        # _Narrative (вердикты advisor)
        return schema.model_validate(
            {
                "verdicts": [
                    {
                        "chart_id": "c3",
                        "text": "GROUP BY по store_id на полном факте: скан 100% "
                        "(EXPLAIN ESTIMATE 20M строк). Сузьте период или возьмите витрину "
                        "с агрегатом по магазинам.",
                    }
                ]
            }
        )


class DevAdvisor:
    def review(self, spec):
        return [
            Finding(
                rule="no_filter_on_large_fact",
                severity=Severity.CRITICAL,
                verdict_class=VerdictClass.DM_CHANGE_REQUEST,
                chart_id="c3",
                title="запрос без фильтра по большому факту",
                evidence={"scan_fraction": 1.0, "rows": 20_000_000},
                suggestions=["ограничить период", "витрина с агрегацией по магазинам"],
            ),
            Finding(
                rule="filter_not_in_sorting_key_prefix",
                severity=Severity.WARN,
                verdict_class=VerdictClass.SPEC_ADJUSTMENT,
                chart_id="c2",
                title="фильтр мимо префикса ключа сортировки",
                evidence={"scan_fraction": 0.62},
                suggestions=["добавить фильтр по date"],
            ),
        ]


def dev_builder(spec, log, session_id):
    bi = spec.target_bi.value  # reflects the UI BI selector (F8)
    for step in (
        f"PROPOSE ok: «{spec.title}», {len(spec.charts)} чартов → {bi}",
        "SQL ok (c1): EXPLAIN + LIMIT-прогон прошли",
        "SQL ok (c2): EXPLAIN + LIMIT-прогон прошли",
        "SQL ok (c3): EXPLAIN + LIMIT-прогон прошли",
        f"{bi}: datasets созданы",
        f"{bi}: чарты созданы, дашборд собран",
    ):
        log(step)
        time.sleep(0.5)
    return DashboardRef(id=3, title=spec.title, url=f"/{bi}/dashboard/3/")


if __name__ == "__main__":
    tmp = Path(__file__).parent.parent / ".tmp"
    store = Store(tmp / "dev_ui.sqlite")
    model_path = tmp / "dev_model.yaml"  # enrichment-правки пишутся сюда, не в repo-модель
    MODEL.dump(model_path)
    app = create_app(
        model=MODEL,
        llm=DevLLM(),
        advisor=DevAdvisor(),
        store=store,
        builder=dev_builder,
        model_path=model_path,
    )
    uvicorn.run(app, host="127.0.0.1", port=8201)
