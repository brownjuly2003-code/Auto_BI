# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

> **СОСТОЯНИЕ СЕЙЧАС (2026-07-04, по «Auto_BI, plan.md. S14 - возьми в работу» из `plan.md`): S14 — СКАЛЯРНЫЙ yoy-KPI, ГОТОВО + СМЁРЖЕНО (main=`2823e92`, PR #8, все 3 CI-job'а зелёные).** Закрывает P-5/B1 из `audit_fable_03_07_26.md`. Это 🟠 S4/IR-задача — по дисциплине проекта написан design-doc «предложи варианты» (`docs/plans/2026-07-04-s14-yoy-kpi-scalar.md`, 3 варианта), **владелец дал «go» на Вариант A** (скаляр-% как отдельная плитка через условную агрегацию) **+ «последний присутствующий бакет»** для v1. Новый `Measure.compare: ScalarCompare {column, grain, kind: yoy|pop, output: pct|abs}` — big_number, значение которого = ОДНО число: последний период vs год/период назад. В отличие от `yoy_pct` (оконный РЯД) сворачивается до ОДНОЙ строки через условную агрегацию по двум бакетам (`sqlgen._generate_compare_kpi_sql`, отдельный путь SQL_GEN, триггер `m.compare is not None`): подзапрос `b` = `max(toStartOf<grain>(date))` (последний присутствующий бакет) и тот же max минус yoy/pop-интервал (yoy = год периодов в единице грейна; pop = один период), внешний запрос агрегирует меру по каждому бакету через `agg(CASE WHEN bucket = b.p_* THEN col END)` → `(cur−prior)/prior` (pct, Float64) или `cur−prior` (abs). Отсутствующий бакет → NULL, не падение → big_number остаётся истинным скаляром (нет окна/измерения). **Контракт на `Measure` (не query)** → `is_percent_measure`/`measure_alias`/адаптеры подхватывают без правок: **нулевые правки адаптеров** (процентный big_number уже рендерится — Superset d3 `.1%` / DataLens percent). Валидация: только big_number, non-day grain, `compare.column`=TIME, взаимоисключимо с transform/denominator. Autospec (≥2 года) добавляет плитку «`<герой>`, г/г» рядом с уровнем героя, **заместив прежнюю полноширинную yoy-линию** (тот же инсайт компактнее → structure/share-вью выживает в 8-чартовом бюджете). **Верификация 3×:** офлайн-гейт (ruff/black · mypy 0/69 · pytest **655** +31 [12 sqlgen compare + 8 validate + autospec/insights] · advisor 9/9 · cov 95%) + DuckDB PG-числа (yoy-pct/pop-abs/NULL-missing) + **CH live-verified на живом Mac-стенде** (`scripts/verify_live_clickhouse.py`: yoy-скаляр = последний месяц vs год назад = независимый ручной расчёт ✓) + **CI integration зелёный** (живой CH+Superset build авто-обзора вкл. compare-KPI, PR #8 run 28696632992). Доки: ARCHITECTURE §3.4 (новый примитив). ff-merge в main (`2823e92`), push-CI на main тоже зелёный (run 28696682000), ветка удалена. Git-статус перед стартом: main=`dc9fdf0` (S12), моя ветка = +1 коммит ff. Внутренние файлы (`plan.md`/`_NEXT_SESSION.md`/`audit_*`) НЕ коммитила.
>
> **Предыдущее (S11) (2026-07-04, по «Auto_BI, plan.md. S11 - возьми в работу» из `plan.md`): S11 — GOLDEN-EVAL В CI, МЕХАНИЗМ ГОТОВ (реальные фикстуры/CI-шаг — НЕ записаны, см. ниже).** Начинает закрывать T-2 из `audit_fable_03_07_26.md`. Владелец выбрал вариант «только design-контракт»: S01 (от которого формально зависит S11 в `plan.md`) ещё не смёржен (live-хвост на квоте Perplexity), поэтому контракт спроектирован и реализован на 26 кейсах, уже живущих на `main` — интеграция новых S01-кейсов (`expect_transforms/ratio/grain/bins/lag`) отдельным шагом при мерже S01.
>
> **Новый шов `auto_bi/llm/fixture.py`** — `FixtureLLMClient` (replay) + `RecordingLLMClient` (record), оба структурно удовлетворяют протокол `LLMClient` (`llm/base.py`), поэтому подключаются В ТОМ ЖЕ месте, что и `GraceKellyClient`/`AnthropicClient`, без нового интерфейса. Фикстура — один JSON-файл на кейс (`<fixtures_dir>/<case_id>.json`, `{"case_id", "calls":[{"step","schema","response"}]}`), повторяющий ровно последовательность вызовов `LLMClient.complete()` этого кейса (grounding → propose_spec → опц. patch). `eval/runner.py::run_golden_case` дак-тайпингом вызывает `begin_case(case_id)`/`end_case()` на `llm`, если они есть (`GraceKellyClient`/`AnthropicClient` их не имеют — не задеты), поэтому один shared LLM-клиент знает, чью фикстуру сейчас читать/писать. Расхождение в последовательности вызовов (промпт/схема поменялись с момента записи) — громкая ошибка `FixtureMissingError`, не тихий повтор чужого ответа. **`auto_bi eval --suite golden --llm-mode {live,replay,record} --fixtures-dir ...`** (новый флаг, дефолт `live` — старое поведение не меняется): `replay` — офлайн, без провайдера/ключа; `record` — гоняет настроенный провайдер и пишет фикстуры для дальнейшего replay. **Смоук-тест CLI пройден** вручную (кейс `g1_revenue_by_day` с руками собранной фикстурой того же качества, что `GOOD_SPEC`/`CLEAR_REPORT` в тестах, — PASS офлайн, без GraceKelly/Anthropic).
>
> **⚠️ НЕ сделано намеренно — требует живого прогона (квота/деньги), это отдельный шаг с явным «да» владельца:** реальные фикстуры для 26 кейсов `main` не записаны (нет живой сессии — GraceKelly сожгла бы Perplexity-квоту, которая и так на паузе ради S01; прямой Anthropic не настроен — ни ключа, ни `anthropic`-extra в этом окружении), поэтому CI `quality`-job НЕ получил шаг `--llm-mode replay` (указывать ему было бы не на что). T-2 закроется, когда появятся реальные фикстуры + CI-шаг — следующая сессия: `auto_bi eval --suite golden --llm-mode record --fixtures-dir tests/fixtures/golden_llm` против живого провайдера (владелец выбирает GraceKelly-квоту или Anthropic-ключ+extra), затем `--llm-mode replay` тем же набором → в `ci.yml` рядом с advisor-шагом, коммит `tests/fixtures/golden_llm/*.json` в репо.
>
> Гейт: ruff/black/mypy 0/69 (новый `llm/fixture.py`) · pytest **635**/1 skipped(нет `anthropic`-extra, ожидаемо)/32 deselected, 95% cov (+11 тестов: 9 `test_llm_fixture.py` + 2 hook-теста в `test_eval.py`) · advisor-eval не запускался отдельно (без изменений). Доки: ARCHITECTURE §3.14 (новая).
>
> Полная история предыдущих сессий (S02, S04, S06, S07, S08, S09, S10, cont.5–cont.16, Phase 0–1) — `docs/history/claude-status-log.md`.

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
