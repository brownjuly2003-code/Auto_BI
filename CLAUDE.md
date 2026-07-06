# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

> **СОСТОЯНИЕ СЕЙЧАС (2026-07-06, P4 плана `new_plen_05_07_26.md`): DataLens-остаток S13 ЗАКРЫТ ЦЕЛИКОМ — B5-период + C1 percent-ось + N2 RU-единицы, всё live-верифицировано скриншотами на стенде (без engine-limit'ов).** (a) **B5**: `DashboardFilter.default` → relative-interval токен (`__interval___relative_-12M___relative_+0d`, грамматика реверснута из datalens-ui 0.3831.0) в `defaultValue`+`defaults` контрола — данные чартов в скоупе РЕАЛЬНО сужаются (линия 24 мес → 12; контракт-тест сверяет сужение через `/api/run` с тем же params-токеном: 24 точки → 2 на `-3M`). (b) **C1** (3 сессии считался engine-limit) решён: ось читает `formatting` поля ТОЛЬКО при `placeholder.settings.axisFormatMode="by-field"` — флаг не выставлялся; плюс горизонтальный bar требует wizard-конвенцию id плейсхолдеров (dimension→`y`, measures→`x` при том же позиционном порядке, data-путь позиционный, axis-путь id-based) — «Доля: Формат» рендерит ось 0,0%/20,0%/40,0%. Память `autobi-bi-engine-limits` «percent на оси не работает» ОПРОВЕРГНУТА и исправлена. (c) **N2**: KPI «236 млрд ₽» / «114 млн» (inline-`/api/run` magnitude-проба → масштаб меры в сабселекте датасета → RU-единица postfix'ом, шрифт KPI `s` — дефолтный клипает) и оси line/bar: тики «8…16» + подпись оси «млрд ₽» (зеркало Superset `ru_kpi_scale`/`_axis_scale`). **Гейт:** ruff/black · mypy 0/69 · pytest **717**/1 skipped/37 deselected (`uv run --with duckdb`) · advisor 9/9 · **DataLens contract 15/15 live** (+3: percent-ось, RU-KPI, B5-сужение). Метод headless-verify без Playwright-MCP + гочи (highcharts CDN блокирован из sandbox → локальная подмена route'ом; inline-config `/api/run`; update-роут чартов) — в runbook `2026-06-13-datalens-selfhosted-runbook.md`. Статусы P1/P2/P3/P5 — в `new_plen_05_07_26.md`. **ОТКРЫВАЕТ:** P10 (вход «P4 или честные engine-limits» выполнен фиксами), P7.
>
> Полная история предыдущих сессий (S02, S04, S06, S07, S08, S09, S10, S13, S14, cont.5–cont.16, Phase 0–1) — `docs/history/claude-status-log.md`.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; версия DataLens-стенда (контрактные маркеры: gateway v4.10.4, dash schemeVersion=8, chart shared version "4", HC=1, gateway-экшен `us/renameEntry` с entryId-стабильностью для атомарного rebuild) запинена в runbook `docs/plans/2026-06-13-datalens-selfhosted-runbook.md`; обновление любой — отдельная задача с прогоном contract-тестов (Superset viz-сьют / `tests/test_datalens_contract.py`).
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
