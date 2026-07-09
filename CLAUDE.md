# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

> **СОСТОЯНИЕ СЕЙЧАС (2026-07-09, Fable): найден и исправлен ЖИВОЙ баг публичного демо — на Space работал только ПЕРВЫЙ билд после старта контейнера, каждый следующий падал 422 «A database with the same name already exists»** (нашёл live-E2E прогоном auto-сессии на Space; корень: у Public-роли (Gamma-like) оставался `can_read on Database` → FAB `is_item_public` идёт ДО `verify_jwt` → даже аутентифицированный GET `/api/v1/database/` адаптера исполнялся анонимом, а анонимному `DatabaseFilter` прячет коннекшены (`all_datasource_access` покрывает датасеты, НЕ Database-строки) → `ensure_database` всегда шёл в create. Диагноз подтверждён пробой с admin-JWT: unfiltered list = count 0, POST = 422). Фикс: `superset_public_role.py` снимает и `can_read on Database` (`9fb6036`); smoke `demo-image.yml` теперь гоняет ДВА билда в одном контейнере (lookup-путь был слепым пятном — одиночный билд всегда идёт в create). **Space ПЕРЕДЕПЛОЕН по «go» владельца (дважды за вечер) и проверен вживую: два билда подряд OK, дашборд анонимно 200 + скриншот.** Вторым заходом по слову владельца («карточки должны иметь одинаковый формат, с центрированием по горизонтали и вертикали»): **KPI-плитки приведены к единому формату** (`e15e7c8`) — процентная плитка теперь «1.5» + «%» строкой ниже как у юнит-плиток (метрика ×100 в SQL, `.1~f` с тримом), пропорции шрифтов запинены, центрирование обеих осей = `KPI_CENTER_CSS` в POST дашборда (алайнмента нет в form_data — CSS есть детерминированный шов нативного формата; рецепт подобран и проверен на живом DOM запиненного 4.1); публикация Space теперь скриптом репозитория `deploy/hf-demo/publish_space.py` (снапшот строго из tracked-файлов), не рецептом из истории сессии. Пост-верификация: **Superset contract 22/22 против демо-Space** (Mac-стенд для Superset-контракта больше не нужен — демо тот же запиненный 4.1.2; артефакты прибраны admin-API), smoke `demo-image.yml` на текущем main зелёный (два билда), ARCHITECTURE дополнен швом KPI-ряда. DataLens-зеркало единообразия — решение «не нужно в той же форме» (юнит инлайн + кегль запинен = ряд однороден by construction; центрирование indicator — engine-default без ручки), детали в `_NEXT_SESSION.md`. Также: 5 dependabot PR смержены (fastapi 0.139 / uvicorn 0.50.2 / clickhouse-connect 1.4.2 / anthropic 0.116 + 4 major-бампа actions в release.yml, CI зелёный); demo-гейт добавлен на `PATCH /dm-change-requests/{id}` (последняя незакрытая shared-state запись, defense in depth) + тест; докстринга лимитера приведена к фактическому поведению (страйк = повторное нарушение окна после истечения локаута, не вызовы во время него). Гейты: ruff/black/mypy 0 · pytest **748**/2 skipped (оба — skipif(SDK установлен); «748/1» от 07.07 шёл без anthropic-extra, регрессии нет), cov 95% · advisor 9/9 · replay 37/37 CH + 16/16 GP · live: `/health` 200, auto-сессия на Space создаётся/approve 202 (билд падает на 422 — и есть баг). Ранее (2026-07-07): P8 ПУБЛИЧНЫЙ ДЕПЛОЙ СДЕЛАН — живое демо `https://juliome20-auto-bi-demo.hf.space` (HF Space `JuLioMe20/auto-bi-demo`, НОВЫЙ HF-акк владельца, RUNNING cpu-basic; вариант владельца: auto-overview БЕЗ LLM/ключа). Один контейнер CH+Superset+auto_bi+nginx (`deploy/hf-demo/`, smoke = workflow `demo-image.yml`); продукт получил `AUTO_BI_DEMO_AUTO_ONLY` (text/fields/enrichment→403, DisabledLLM, вкладки UI гаснут по /health), `AUTO_BI_SUPERSET_PUBLIC_URL`, фронт на относительных путях (работает под префиксом `/agent/`); F-2 (uvicorn proxy_headers + `AUTO_BI_FORWARDED_ALLOW_IPS`, nginx-пример с X-Forwarded-For) и L-4 (purge неактивных ключей лимитера) закрыты кодом+тестами (pytest **748**/95%). Гочи деплоя: Public-роль ОБЯЗАНА быть read-only (write-пермишн у Public = FAB is_item_public пускает запись анонимом МИМО JWT → 500 на owners-flush); SUPERSET_SECRET_KEY строго один на контейнер (per-import ключ рвёт JWT между воркерами); uv-managed python — на world-readable путь (`UV_PYTHON_INSTALL_DIR`), стор-каталог создавать в рантайме (UID 1000). Live-verify: curl (403-гейт/билд/аноним-200) + Playwright-скрины (UI с задизейбленными вкладками; дашборд с данными «12 млрд ₽»). PyPI (X-2): job готов, ждёт trusted publisher от владельца. Ранее этой же ночью: **X-4 session-resume ЗАКРЫТ** — рестарт `auto_bi serve` больше не теряет диалоги: промах реестра лениво регидрирует сессию из Store (schema v7 `sessions.owner/target_bi/pinned`; фаза из spec-строк, clarify-раунды из trace, билд/абсолютный url из builds + синтетический терминальный SSE-event, RBAC-скоуп владельца восстановлен, DELETE=tombstone; заодно закрыта потеря сессий на eviction за MAX_SESSIONS). ARCHITECTURE §3.15, DEPLOYMENT §9, тесты `tests/test_api_resume.py` (12). Гейты: ruff/black/mypy 0/69 · pytest **738**/95% · advisor 9/9 · replay 37/37+16/16 · живой smoke kill/restart PASS. Ранее в тот же день: **ПЛАН `new_plen_05_07_26.md` ЗАКРЫТ 9/10 (P8 опционален); фиксы аудита P10 СДЕЛАНЫ — открытых MED нет, вердикт-условие 9.8 выполнено** (аудит: `audit_fable_p10_07_07_26.md`, было 9.7 с двумя MED). Закрыто этой сессией: **F-1** — web-UI отдаёт абсолютную ссылку на дашборд (`create_app(bi_base_urls=...)`, wiring из `settings.*_url` в `cli.py::_serve`; live: auto-сборка → `http://localhost:8088/superset/dashboard/…` кликается); **F-3** — RU-масштаб оси только при `len(measures)==1` (оба адаптера; live: двумерный line на стенде без деления, метрики `SUM(...)` чистые); **L-1** — KPI в полосе 1–10 ед. держит один десятичный знак (Superset `",.1f"` / DataLens `precision:1`, `_ru_scale` теперь отдаёт (divisor, unit, scaled)); **L-3** — OpenAPI-версия из `__version__`. Гейты: ruff/black/mypy 0/69 · pytest **722**/95% · advisor 9/9 · replay 37/37+16/16 · **Superset contract 22/22 live · DataLens contract 15/15 live**; verify-дашборды #37/#39 удалены со стенда. **Остаток (не для 9.8):** F-2 — прокси-wiring квот, строго перед P8-деплоем; L-2/L-4 — не трогать без запроса. Гочи стенда (kind `hq-demo` остановлен, кириллица в argv Git Bash → `?`) — в `docs/history/claude-status-log.md`.
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
