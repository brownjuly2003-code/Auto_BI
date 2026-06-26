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

import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn

from auto_bi.adapters.base import DashboardRef
from auto_bi.advisor.findings import Finding, Severity, VerdictClass
from auto_bi.api import create_app
from auto_bi.semantic.model import Column, ColumnRole, Join, Physical, SemanticModel, Table
from auto_bi.store import Store

# cardinality + fk + the join are here so the «Авто» mode shows real breakdowns/pie
MODEL = SemanticModel(
    tables=[
        Table(
            name="dm.sales_daily",
            description="Дневные продажи",
            grain=["date", "store_id"],
            columns=[
                Column(name="date", type="Date", role=ColumnRole.TIME, description="День продажи"),
                Column(
                    name="store_id", type="UInt32", role=ColumnRole.DIMENSION, fk="dm.stores.id"
                ),
                Column(
                    name="revenue",
                    type="Decimal(18,2)",
                    role=ColumnRole.MEASURE,
                    agg="sum",
                    description="Выручка, руб",
                ),
                Column(
                    name="orders",
                    type="UInt32",
                    role=ColumnRole.MEASURE,
                    agg="sum",
                    description="Число заказов",
                ),
            ],
            physical=Physical(engine="clickhouse", rows=20_000_000, cardinality={"store_id": 4200}),
        ),
        Table(
            name="dm.stores",
            description="Справочник магазинов",
            grain=["id"],
            columns=[
                Column(name="id", type="UInt32", role=ColumnRole.DIMENSION),
                Column(name="name", type="String", role=ColumnRole.DIMENSION),
                Column(
                    name="city",
                    type="LowCardinality(String)",
                    role=ColumnRole.DIMENSION,
                    description="Город",
                ),
                Column(
                    name="region",
                    type="LowCardinality(String)",
                    role=ColumnRole.DIMENSION,
                    description="Регион",
                ),
                Column(
                    name="format",
                    type="LowCardinality(String)",
                    role=ColumnRole.DIMENSION,
                    description="Формат",
                ),
            ],
            physical=Physical(
                engine="clickhouse",
                rows=4200,
                cardinality={"id": 4200, "name": 4203, "city": 20, "region": 8, "format": 3},
            ),
        ),
    ],
    joins=[Join(left="dm.sales_daily.store_id", right="dm.stores.id")],
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
    """Отвечает по имени запрошенной схемы; первый grounding — с вопросами.

    Логирует каждый вызов в Store с полями step/completion_chars, как это делает
    GraceKellyClient — чтобы панель «Наблюдаемость» показывала ненулевые расходы.
    """

    def __init__(self, store=None) -> None:
        self.grounding_calls = 0
        self.spec_calls = 0
        self._store = store

    def complete(self, prompt, schema, *, reasoning=False, session_id=None, step=""):
        started = time.monotonic()
        time.sleep(0.4)  # чтобы «агент думает» был виден
        name = schema.__name__
        if name == "GroundingReport":
            self.grounding_calls += 1
            result = schema.model_validate(AMBIGUOUS if self.grounding_calls == 1 else CLEAR)
        elif name == "DashboardSpec":
            self.spec_calls += 1
            spec = dict(SPEC)
            if self.spec_calls > 1:
                # title капится на v2: вторая и последующие правки возвращают тот же
                # spec — браузерная проверка noop-ветки («правка не изменила спецификацию»)
                spec = {**SPEC, "title": f"Обзор продаж · v{min(self.spec_calls, 2)}"}
            result = schema.model_validate(spec)
        else:  # _Narrative (вердикты advisor)
            result = schema.model_validate(
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
        if self._store is not None:
            self._store.log_llm_call(
                session_id=session_id,
                model="dev-scripted",
                prompt_sha256="dev",
                prompt_chars=len(prompt),
                reasoning=reasoning,
                status="completed",
                latency_ms=round((time.monotonic() - started) * 1000),
                step=step,
                completion_chars=len(result.model_dump_json()),
            )
        return result


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


def dev_run_query(sql):
    """Crafted rows so the «Что видно» insight panel populates without a DWH.

    Routes a chart's generated SQL by the column it selects, and is shaped to exercise every
    observation kind: the daily revenue series spans twelve weeks, climbing through the first
    half then turning down (a reversal the overall trend hides), with a clear weekend lift (a
    day-of-week seasonality) and one anomalous spike day; the region ranking is concentrated
    (leader + top-3 concentration); the city ranking is evenly spread (leader + spread, the
    complement); the format chart is a 3-way share. Large numbers exercise the compact RU
    formatting (млрд / млн)."""
    if "share_of_total" in sql:
        return [
            {"format": f, "share_of_total_sum_revenue": v}
            for f, v in [("магазин у дома", 0.41), ("супермаркет", 0.35), ("гипермаркет", 0.24)]
        ]
    if '"date"' in sql:
        start = date(2026, 1, 5)  # a Monday → twelve whole weeks of daily coverage

        def _rev(i: int) -> float:
            d = start + timedelta(days=i)
            ramp = i if i <= 42 else (84 - i)  # up to week 6, then back down → a reversal
            base = 2.0e8 + 6.0e6 * ramp
            if d.weekday() >= 5:  # Saturday/Sunday run higher → weekday seasonality
                base *= 1.4
            return 1.8e9 if i == 23 else base  # one anomalous spike (a Wednesday)

        return [
            {"date": (start + timedelta(days=i)).isoformat(), "sum_revenue": _rev(i)}
            for i in range(84)
        ]
    if "region" in sql:
        return [
            {"region": r, "sum_revenue": float(v)}
            for r, v in [
                ("Центр", 5.0e9),
                ("Приволжье", 1.2e9),
                ("Урал", 9.0e8),
                ("Сибирь", 7.0e8),
                ("Юг", 5.0e8),
            ]
        ]
    if "city" in sql:
        return [
            {"city": c, "sum_revenue": float(v)}
            for c, v in [
                ("Самара", 4.2e8),
                ("Ижевск", 4.1e8),
                ("Пермь", 4.0e8),
                ("Омск", 3.9e8),
                ("Уфа", 3.8e8),
                ("Казань", 3.7e8),
                ("Тверь", 3.6e8),
                ("Тула", 3.5e8),
                ("Сочи", 3.4e8),
            ]
        ]
    return []


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
    # AUTO_BI_DEV_AUTH=1 -> включить auth/RBAC для браузерной проверки логина (Phase 4).
    # alice видит только схему dm (вся демо-модель); bob — пустую (нет доступа к dm).
    auth_demo = os.environ.get("AUTO_BI_DEV_AUTH") == "1"
    if auth_demo:
        from auto_bi.auth import hash_password

        store.upsert_user("alice", hash_password("secret"), "analyst", ["dm"])
        store.upsert_user("bob", hash_password("secret"), "analyst", ["finance"])
        print("auth demo ON — войдите как alice / secret (или bob / secret = нет доступа к dm)")
    app = create_app(
        model=MODEL,
        llm=DevLLM(store=store),
        advisor=DevAdvisor(),
        run_query=dev_run_query,
        store=store,
        builder=dev_builder,
        model_path=model_path,
        auth_enabled=auth_demo,
    )
    uvicorn.run(app, host="127.0.0.1", port=8201)
