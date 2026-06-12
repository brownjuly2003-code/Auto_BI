# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

**Phase 0 ЗАВЕРШЕНА и верифицирована** (2026-06-11, ветка `phase-0/vertical-slice`). Exit criteria проверены реальными прогонами: стенд на Mac (`~/auto_bi_stand`, compose: Superset 4.1.2 + CH 24.8, демо-DM 20M строк), интроспекция → `semantic/model.yaml` закоммичен, 4 contract-теста form_data прошли против живого Superset, e2e `auto_bi build "Обзор продаж: …"` собрал дашборд из 3 чартов (`/superset/dashboard/1/`), GraceKelly-вызовы в `logs/llm_calls.jsonl`. Доступ к стенду с Windows — SSH-туннель `ssh -N -L 8123:localhost:8123 -L 8088:localhost:8088 deproject-mac`; Docker ТОЛЬКО на Mac.

Известное ограничение Phase 0 (by design): джойнов в IR нет — запросы с полями из смежных таблиц («топ городов» при city в dm.stores) отклоняются валидацией; промпт предупреждает LLM. Снимается в Phase 1/2 по PLAN.

**Следующий шаг: Phase 1 (S6 — не начинать без ревью; предложен `/cxkm` перед стартом).** Перед работой свериться с PLAN.md и обновить этот блок по завершении фазы/вехи.

> 2026-06-12: `/cxkm` по диффу `main...HEAD` запущен — оба внешних ревьюера недоступны (CX `codex app-server exited unexpectedly`; KM/mco kimi `LLM not set` — провайдер не сконфигурирован). Postflight локально зелёный: `ruff` clean, `pytest` 55 passed / 4 deselected (live-тесты гейтятся стендом). S6-ревью НЕ пройдено → Phase 1 не начинать. Re-run: `codex-prompt-phase0-review.md` (root, untracked) + готовые `.tmp/diff.patch`, `.tmp/km-prompt.md`. Hygiene: `.gitignore` теперь покрывает `.env.*` (коммит `5836695`).

> 2026-06-12: внешнее ревью Phase 0 проведено локальной моделью (Fable) → 8 findings в `fable_audit.md` (3×P2 + 5×P3). **Все закрыты в коде той же сессией** (по явному запросу пользователя): F1 SQL-инъекция через `measure.label` в form_data (экранирование), F2 `order_by` по мере → невалидный CH-SQL (маппинг на алиас + `measure_alias` в `ir/spec.py`), F3 мёртвый `AUTO_BI_SEND_SAMPLES` (проброшен в `render_model`, ARCHITECTURE §4 уточнён), + P3: пустой `IN`, connect-retries клиентов, ранний выход repair-петель, уникальность имени dataset, defensive `validate_spec` в pipeline. `ruff`/`black` clean, `pytest` **65 passed / 4 deselected**. Закоммичено в ветку: `f7a6780`. **Live-смоук P2 против работающего CH 24.8 стенда (Mac) пройден**: F2-fixed `ORDER BY "sum_revenue"` → 5 строк rc=0; F2-buggy контроль `ORDER BY "revenue"` → `Code 215 NOT_AN_AGGREGATE` (баг подтверждён, фикс снимает); F1 escaped-label → SUM факта (236e9), не `system.numbers` (инъекции нет). **S6 по-прежнему открыт** — внешнее CX/KM-ревью не пройдено; Phase 1 не начинать.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; обновление — отдельная задача с прогоном contract-тестов.
8. Изменения промптов мержатся только с прогоном eval-сьюта (с Phase 1).

## LLM

GraceKelly (локальный сервис, должен быть запущен): `POST http://127.0.0.1:8011/api/v1/orchestrate`
`{"prompt": ..., "model": "claude-sonnet-4-6", "reasoning": true, "decompose": false, "session_id": ..., "metadata": {"app": "auto_bi"}}`
- `prompt` ≤ 40 000 символов → context selection обязателен на больших DM.
- Text-in/text-out: структурированный вывод через JSON-блок → pydantic → repair loop.
- Все вызовы через `llm/LLMClient`-абстракцию, напрямую httpx из бизнес-кода не дёргать.
- `reasoning: true` — на GROUNDING/PROPOSE_SPEC; механические шаги — без reasoning.

## Stack и конвенции

- Python 3.12 + uv; ruff + black; pytest. FastAPI, pydantic v2, sqlglot, httpx.
- Без LangChain/LangGraph — агент = простая state machine.
- Код/идентификаторы — английский; доки и общение — русский.
- Секреты только в `.env` (в `.gitignore`); в логи/доки не попадают.
- Локальный стенд: `docker-compose up` (Superset + ClickHouse с демо-DM).

## Стопперы переключения модели (/auto)

Дефолт реализации — **Opus high**. Агент не может сменить модель сам, поэтому стоппер = остановка с протоколом (ниже). Триггеры объективные, «по ощущениям» не останавливаться.

| Код | Триггер (проверяемый) | Действие |
|---|---|---|
| S1 дебаг-цикл | Один и тот же баг/тест: 3 цикла «гипотеза → правка → прогон» без смены симптома (новая ошибка = прогресс, счётчик сбрасывается) | STOP → рекомендация **Fable**; можно взять независимую задачу фазы |
| S2 промпты/eval | Задачи промпт-инжиниринга агента и дизайна eval (в Phase 1 это 1.4–1.7: GROUNDING/CLARIFY/PROPOSE_SPEC, политика уточнений, golden-кейсы) | **Не начинать на Opus в /auto.** STOP до старта → Fable или ручная сессия |
| S3 eval-регрессия | После правки промптов eval ниже базовой на ≥10 п.п. или ниже exit-порога фазы, и 1 итерация починки не вернула | STOP → Fable; промпт-изменение не мержить |
| S4 инварианты | Решение требует менять IR-схему, интерфейс `BIAdapter` или инварианты 1–8 этого файла | STOP всегда — вопрос пользователю, модель не важна |
| S5 form_data | viz_type не проходит contract-тест после 3 вариантов шаблона | STOP → приложить diff «create vs GET»; ручная сессия (вероятно с браузером), модель вторична |
| S6 граница фазы | Exit criteria фазы выполнены и проверены | STOP → предложить `/cxkm`-ревью перед следующей фазой; следующую фазу самому не начинать |
| S7 инфра | GraceKelly / Superset / ClickHouse-стенд недоступен после 2 попыток восстановления | STOP **без** эскалации модели — это не модельная проблема |

**Протокол STOP:** (1) WIP-коммит в текущую ветку `wip(stopper:Sx): <задача>`; (2) одна строка в блок «Статус» этого файла: дата, задача, стоппер, рекомендованная модель; (3) финальное сообщение: причина, что переключить, точка продолжения. После S1/S3/S5 допустимо взять независимую задачу той же фазы; после S2/S4/S6 — только остановка.

## Definition of done (фаза/задача)

- Тесты зелёные (pytest), ruff чистый.
- Exit criteria фазы из PLAN.md проверены реальными прогонами, не «на глаз».
- Обновлены: статус в этом файле; ARCHITECTURE.md — если дизайн уточнился.
