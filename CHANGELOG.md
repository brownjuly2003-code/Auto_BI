# Changelog

Формат по [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по [SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- **Браузерный E2E веб-UI + axe-скан** (D-4): `tests/test_web_e2e.py` — Chromium
  (Playwright) проходит путь пользователя против настоящего `auto_bi serve` в
  demo-профиле (DisabledLLM — без ключа и трат) и живого ClickHouse+Superset:
  Авто-обзор → превью спеки → сборка → SSE-лог → ссылка на дашборд, с axe-core-сканом
  каждого состояния UI. Гоняется в integration-job CI на каждом PR; маркер `e2e`
  деселектнут по умолчанию. Первая же находка скана починена: на странице не было
  `<h1>` — бренд в шапке стал заголовком первого уровня без визуальных изменений.

- **Retention-свип store** (D-3, аудит 2026-07-18): `Store.purge_retention` подрезает по
  возрасту три таблицы, которые росли без ограничений — `llm_calls` (строка на вызов
  провайдера), `trace_events` (строка на шаг пайплайна) и **не-`live`** строки
  `bi_artifacts` (прошлые ревизии от пересборок). До этого свипались только `auth_tokens`.
  Выключено по умолчанию (`AUTO_BI_RETENTION_ENABLED=false`) — удаление операционной
  истории необратимо; сроки настраиваются по таблицам, `0` = хранить вечно. Свип идёт на
  старте `serve` и раз в `AUTO_BI_RETENTION_SWEEP_HOURS`. **Не трогает** `sessions`/
  `messages`/`specs`/`builds` (работа пользователя, не телеметрия) и `live`-строки леджера
  (по ним живёт ownership-cleanup — их удаление осиротило бы реальный дашборд в BI).
- **Prometheus-метрики** (D-3): `GET /api/v1/metrics` в text exposition format, opt-in
  через `AUTO_BI_METRICS_ENABLED`. Билды по статусу и in-flight, слоты сборки, DWH-проходы
  и суммарное время в них, LLM-вызовы/токены/секунды по моделям, `auto_bi_llm_cost_usd_total`
  по той же прайс-таблице, что и бюджетный guard (`llm.budget.cost_usd` — общая функция,
  незалистанная модель тарифицируется в 0), и `auto_bi_store_rows{table}` — по ней видно,
  работает ли retention. Цифры глобальные, поэтому при включённом auth эндпоинт
  **admin-only**; выключенный отвечает 404, а не 403. Рендер — свой (модуль 174 строки), без
  зависимости `prometheus_client` с её глобальным реестром и default-коллекторами.

### Changed

- **Общий план запроса вместо двух EXPLAIN одного стейтмента** (D-2 §3, ядро пункта 21):
  guard и Advisor планировали SQL чарта каждый сам — `EXPLAIN` и `EXPLAIN ESTIMATE` подряд
  по одному и тому же запросу. Теперь на одношаговых путях сборки (`auto_bi build`,
  `build --auto`) обе стороны делят `PlanCache` (`agent/query_plan.py`) на один вызов
  сборки: Advisor записывает результат своего плана, guard читает и пропускает собственный
  `EXPLAIN`, если этот же стейтмент уже спланирован без ошибки. **Замер приёмки на
  8-чартовом auto-overview: 25 → 17 проходов в DWH, 3,1 → 2,1 на чарт.**
  Ключ — **точный текст SQL**: Advisor судит effective query (P1-2) по до-нормализационной
  спеке, а guard — после-нормализационную, и они расходятся, как только срабатывает B3
  label-join или контрол с дефолтом (для auto-overview совпадают все 8 чартов, для
  LLM-спеки с FK-измерением — нет). Промах остаётся промахом: переиспользование evidence
  между разными стейтментами вернуло бы Advisor'у замер запроса, который BI не выполняет.
  Инвариант 3 не тронут — `guard_sql` и LIMIT-прогон безусловны, а попадание в кэш значит,
  что DWH уже разобрал и проверил права на этот самый стейтмент; `ok` выводится
  консервативно (пустой ESTIMATE = промах). API-путь approve кэш не получает (preview и
  build — разные запросы), поведение там прежнее. ARCHITECTURE §3.19.

### Fixed

- **Гонка `Store.close()` с фоновым build-тредом**: store делит одно sqlite3-соединение
  между тредпулом API и daemon-тредом сборки под внутренним локом — кроме `close()`,
  который закрывал соединение без лока. Тред сборки пишет финальный `build_done`-trace
  ПОСЛЕ того, как клиент уже увидел done-событие по SSE, поэтому close на teardown мог
  попасть в середину `execute` и уронить интерпретатор в C-слое sqlite3 (падение
  offline-CI 20.07, exit 139 внутри `add_trace_event`). Теперь `close()` берёт тот же
  лок: незавершённый стейтмент дописывается, опоздавший писатель получает чистый
  `sqlite3.ProgrammingError`, который трейсинг сборки и так глотает. Оба поведения
  закреплены тестами (мутационная проверка: без лока тест падает).
- **Доступность веб-UI** (D-5, п.24): три дефекта, замеренные в браузере, а не на глаз.
  (1) **Контраст**: `--text-3` был `#9aa1ab` = **2,41:1** на `--bg` при требовании WCAG AA
  4,5:1 — и им набраны **26 селекторов** текста 10–15px, включая ярлыки вкладок и заголовки
  панелей; теперь `#636b77` (4,7–5,4:1 на всех трёх фонах). `--text-2` заодно затемнён до
  `#4f5766`, чтобы три уровня текста остались различимы, а не слились в один серый.
  (2) **Метки**: поля логина, запроса к агенту и комментария к раскладке опознавались
  только по `placeholder` — он не метка (исчезает при вводе). Добавлены настоящие `label`
  (визуально скрытые там, где по дизайну нет места), заголовок формы входа стал `h2`.
  (3) **Живые области**: сборка идёт минутами и пишется по SSE, но не объявлялась вообще
  (`aria-*` в `app.js` не было ни одного) — лог сборки получил `aria-live="polite"`,
  результат и ошибка входа — `role="alert"`, чат — `role="log"`, чип сессии — `role="status"`.
  Плюс вкладки режима доведены до паттерна WAI-ARIA: `aria-controls`/`role="tabpanel"`,
  roving `tabindex` и навигация стрелками/Home/End (без неё до вкладок «Полями»/«Авто» с
  клавиатуры было не добраться). Проверено в браузере: 0 нарушений контраста и 0 контролов
  без доступного имени из 20; возврат прежнего цвета немедленно даёт 20 нарушений.
  Закреплено статическим тестом (`tests/test_ui_a11y.py`), который сам проверен на
  заведомо плохом вводе.
- **Lifecycle httpx-клиентов** (D-2 §4, дизайн-док 2026-07-19 — независимая часть, взята
  отдельным PR): адаптеры создаются на каждый билд (`make_adapter` внутри
  `compile_and_build`) и на каждую readiness-пробу (`/ready`), но их HTTP-пулы никогда не
  освобождались — в `serve` пулы копились до сборки мусора, по одному на билд и на каждый
  пинг `/ready` (демо-keepalive пингует его постоянно). Теперь: у обоих адаптеров есть
  `close()` (concrete-хелпер, `BIAdapter`-Protocol не тронут — S4, как
  `drain_build_artifacts`); `compile_and_build` освобождает адаптер в `finally` на любом
  исходе (после леджера/прунинга; сбой самого close логируется и никогда не маскирует
  результат билда); `/ready` ходит через `probe_health` (одноразовый адаптер закрывается
  тут же); `auto_bi prune` закрывает адаптер каждого таргета. `GraceKellyClient` получил
  отсутствовавший `close()`, `AnthropicClient` теперь удерживает SDK-клиент (а не только
  bound `messages.create`) и закрывает его пул; `serve` освобождает LLM-клиент при
  остановке. Закрытие в pipeline и probe_health закреплено мутационно-проверенными тестами.
- `validate_spec` отклоняет коллизию SELECT-алиасов между мерой и размерностью: label меры,
  совпавший с bare-именем колонки-измерения (`label="store_id"` рядом с
  `dimensions=["store_id"]`), давал датасету две колонки под одним именем — какую из них
  возьмёт агрегат BI, не определено, чарт тихо показывал неверные числа. Проверки коллизий
  «внутри мер» и «внутри размерностей» уже были; закрыт третий случай (свободный аудит
  2026-07-19, находка 2).

## [0.4.0] - 2026-07-18

### Added

- Гейт качества тестов (D-4-минимум, аудит 2026-07-18): CI падает при покрытии <90%
  (`--cov-fail-under=90`; фактическое ~95%), warnings в тестах = ошибки
  (`filterwarnings = ["error"]` c одним явным allowlist-исключением на сторонний
  starlette/httpx2-deprecation, который из кода auto_bi не чинится).
- BI-artifact ownership **live-cleanup ВКЛЮЧЁН** (P0-2, критерий 4, надстройка над OFFLINE-леджером
  ниже; live-проверен на 20M-стенде 2026-07-18). Два пути через один движок удаления
  `prune_artifact_rows` (delete-by-id, порядок `chart → dashboard → dataset` — датасет не
  удаляется, пока его читает чарт; `SHARED_BI_KINDS` пропускаются). (1) **Авто-прунинг на
  ребилде:** после успешного билда И записи в леджер `compile_and_build` зовёт
  `_prune_superseded_artifacts` — удаляет BI-артефакты ПРОШЛЫХ ревизий ЭТОЙ сессии по
  `native_id` (селекция `orphan_bi_artifacts`: ключ session/owner/build_token, **НИКОГДА** имя/
  title; только что доставленный дашборд несёт текущий `build_token` и не трогается). Прунинг
  **НИКОГДА не валит билд**: дашборд уже доставлен, любая ошибка логируется, строки остаются
  `live` и повторяются следующим прунингом. Выключатель `AUTO_BI_PRUNE_ON_REBUILD=false`
  (`Settings.prune_on_rebuild`, дефолт `true`, прокинут параметром `prune_orphans` в
  `build_dashboard`/`compile_and_build`). (2) **Операторская команда** `auto_bi prune
  [--session ID] [--dry-run] [--model-path …]` — селекция `Store.stale_bi_artifacts`: живые
  строки билдов, которые НЕ являются последним билдом своей сессии (последний дашборд каждой
  сессии всегда переживает прунинг — удаляются ревизии, не чужие дашборды). `--dry-run` печатает
  кандидатов и не удаляет; недоступный BI-таргет → его строки пропускаются; exit 0 при чистом
  прогоне, 1 если что-то не удалось или было пропущено. `delete_artifact` — новый concrete-хелпер
  обоих адаптеров (Superset `_DELETE_PATHS` / DataLens `_DELETE_SCOPES`; **вне** `BIAdapter`-
  Protocol, как `drain_build_artifacts`/`set_artifact_namespace`): удаляет одну сущность по
  native_id, 404 = уже удалена (норм), любой другой сбой → строка остаётся `live`; shared-kind
  (`database`-connection) отвергается и в SQL-селекции, и самим адаптером. `stale_bi_artifacts`
  добавлен в Store, `mark_bi_artifacts_superseded(ids)` теперь используется обоими путями.
  IR/`BIAdapter`-Protocol/инварианты 1–8 не тронуты (ARCHITECTURE §3.17).
- BI-artifact ownership ledger + orphan-cleanup SELECTION (P0-2, критерий 4, **OFFLINE-слой**):
  таблица Store `bi_artifacts` (`id`, `session_id`, `build_token` = build-namespace = ревизия,
  `target_bi`, `kind` = `database|dataset|chart|dashboard`, `native_id`, `name` — display/debug,
  `owner`, `schema_set` — DWH `schema.table` для RBAC, `status` = `live`, `created_at`). Оба
  адаптера (Superset и DataLens) при `build()` накапливают созданные сущности и отдают их новым
  concrete-методом `drain_build_artifacts()` (**НЕ** в `BIAdapter`-Protocol, как
  `set_artifact_namespace`); оркестратор `compile_and_build` после успешного билда сливает их и
  пишет в леджер с session/owner/build_token (`owner` — из `sessions.owner`, NULL при auth off;
  `schema_set` датасета/чарта — из `query.table`). Выбор кандидатов на чистку —
  `orphan_bi_artifacts(session_id, current_build_token, *, owner=None)`: живые строки сессии с
  `build_token != текущего` (опц. RBAC по `owner`), ключ — **ВЛАДЕНИЕ** (session/build_token),
  **НИКОГДА** не имя/заголовок (два дашборда могут делить техническое имя — та самая ловушка
  `_delete_if_exists`-по-имени DataLens, ради которой леджер и существует).
  `mark_bi_artifacts_superseded(ids)` — шов для будущей чистки, пока не используется. Селекция
  исключает `SHARED_BI_KINDS` (`database`-connection, идемпотентен-по-имени и общий между
  билдами) **по умолчанию** — выдача безопасна для delete-by-id как есть; `include_shared=True`
  даёт полный аудит-вид. Таблица добавлена как always-run `CREATE IF NOT EXISTS` + индексы,
  **БЕЗ** bump'а версии схемы (как budget-индексы `llm_calls`). **Ничего живого не удаляется**
  (осознанный scope): сам продукт BI delete API не вызывает, `_delete_if_exists`/
  `_promote_to_canonical` DataLens не тронуты; полный цикл build → rebuild → orphan → Superset
  DELETE → superseded live-проверен на 20M-стенде 2026-07-18. IR/`BIAdapter`-Protocol/
  инварианты 1–8 не тронуты (ARCHITECTURE §3.5).
- Бюджет LLM на шве клиента (P0-3 item 4): `LLMBudget` (`llm/budget.py`) энфорсит лимиты по
  **вызовам / токенам / стоимости / времени** на **сессию** и на **актора / скользящие 24ч**
  (владелец сессии при auth on; единый глобальный бакет при auth off — предохранитель суммарного
  расхода анонимного демо). Проверка живёт в `complete_with_repair` (хук `on_attempt`) и срабатывает
  **перед каждой** попыткой, поэтому первичный вызов И каждый репэйр списывают бюджет; обойти шов
  нельзя. Fail closed: `BudgetExceeded(LLMError)` до вызова, который пересёк бы лимит, с указанием
  измерения. Токены реальные у Anthropic, оценка `chars/4` у GraceKelly; время — кумулятивная
  латентность; стоимость — таблица цен $/1000 токенов на модель (опционально). Агрегат берётся из
  существующего леджера `llm_calls` (без параллельной таблицы; переживает запросы/рестарты),
  индексы `ix_llm_calls_session`/`ix_llm_calls_created`. Конфиг `AUTO_BI_LLM_BUDGET_*` через
  `make_llm`. **Opt-in, по умолчанию выключен** (как session/work-квоты) — CLI/тесты/локалка не
  затронуты, LLM-демо включает явно (ARCHITECTURE §3.6, DEPLOYMENT §3). IR/`BIAdapter`/инварианты
  1–8 не тронуты.
- Release preflight (P1-7): в `release.yml` добавлен job `preflight`, который выполняется
  ПЕРВЫМ и валит весь релиз, пока не сойдётся всё разом — версия тега (`vX.Y.Z`) ==
  `pyproject.toml [project].version` == `auto_bi.__version__`; в `CHANGELOG.md` есть непустая
  секция `## [<версия>]` (а не только `[Unreleased]`); `uv build` даёт один связный sdist+wheel
  под этой версией; `twine check` проходит; чистая установка колеса в свежее окружение импортится
  и запускает консольный скрипт (`auto_bi --help`). Job'ы `release` (GHCR + GitHub Release) и
  `pypi` (Trusted Publishing) теперь `needs: preflight`, а `pypi` публикует РОВНО те артефакты,
  что собрал и проверил preflight (`upload-artifact`/`download-artifact`, без пересборки) —
  рассинхрон версий отклоняется до любой публикации, частичный релиз невозможен. Проверки
  когерентности и секции changelog вынесены в юнит-тестируемый `scripts/release_preflight.py`
  (`tests/test_release_preflight.py`); быстрый drift-гвард «pyproject == `__version__`» добавлен
  в обычный сьют, чтобы расхождение ловилось на каждом PR, а не только при теге.
- Supply-chain hardening релиза (P1-7 доп., GitHub-native, без внешних сервисов): (1)
  **SLSA build provenance** — новый job `provenance` в `release.yml` (`needs: preflight`)
  скачивает РОВНО те sdist+wheel, что собрал preflight, и подписывает их через
  `actions/attest-build-provenance` (Sigstore + GitHub attestation store); проверка постфактум —
  `gh attestation verify <файл> --repo brownjuly2003-code/Auto_BI`. Права выданы минимально на
  уровне job'а (`id-token: write`, `attestations: write`, `contents: read`), не workflow-wide.
  (2) **PEP 740 аттестации на PyPI** — у `pypa/gh-action-pypi-publish` под trusted publishing
  они включены по умолчанию (v1.11+, `@release/v1` уже трекает такую версию); выставлены
  `attestations: true` явно, чтобы будущий флип дефолта не уронил их тихо. (3) **SBOM** —
  `anchore/sbom-action` генерирует SPDX-JSON по исходному дереву (`pyproject.toml` + `uv.lock`)
  и файл прикладывается к GitHub Release ассетом (через `files:` у `action-gh-release`; событие
  тега = `push`, поэтому собственная выгрузка ассета у экшена не срабатывает — отключена во
  избежание лишнего workflow-артефакта). (4) **Environment-approval** — job `pypi` получил
  полную форму `environment:` с `url` на точную версию пакета на PyPI; включение required
  reviewers для окружения `pypi` (ручной аппрув каждой публикации) — настройка в repo settings,
  файл менять не нужно (как включить — `docs/DEPLOYMENT.md` §2). Аттестации/SBOM исполняются
  только на реальный push тега `vX.Y.Z` — первая живая проверка на следующем релизе.
- Escape hatch `raw_sql` (X-5): ручной SELECT для запросов, которые IR не выражает.
  `ChartQuery.raw_sql` (только `viz=table`) SQL_GEN отдаёт дословно, дальше — тот же
  live-гейт, что у сгенерированного SQL (`guard_sql` SELECT-only → EXPLAIN → LIMIT-прогон)
  → virtual dataset в Superset. Ручной люк (CLI `auto_bi raw --sql-file q.sql`), НЕ
  LLM-генерация — инвариант 1 сохраняется. Ров осознанно ослаблен: advisor слепнет,
  нормализации/форматы не применяются, колонки по модели не проверяются (ARCHITECTURE §3.16).
- Публичное демо: текстовый путь можно включить (`AUTO_BI_DEMO_AUTO_ONLY=false`) — `start-autobi.sh`
  подключает LLM-провайдер (по умолчанию GraceKelly `claude-sonnet-5` через публичный туннель в
  Space secrets) и принудительно включает per-IP session-квоту. По умолчанию демо остаётся
  auto-only (без LLM, нулевой бюджет).

- Semantic governance для rate/non-additive колонок (P1-6): поле `Column.additivity`
  (`additive | semi_additive | non_additive`; `semi_additive` записывается, но в v1 не
  энфорсится). Интроспекторы CH/GP размечают rate-подобные имена (`rate|ratio|pct|percent|share`,
  `price`/`unit_price`) как `agg: avg` + `non_additive`; валидация спеки отклоняет `sum` над
  `non_additive` колонкой (и в denominator ratio-меры) с подсказкой для repair loop;
  enrichment-API отвечает 422 на такой `agg`; autospec для неаддитивной меры без модельного agg
  падает в AVG и не строит share-of-total над ней; маркер рендерится в model_text для LLM.
  Committed-модели исправлены: `price` (model, model_stand) больше не суммируется
  (ARCHITECTURE §3.2).
- `Physical.captured_at` — UTC-штамп снятия статистики интроспектором: замороженные в git
  `rows`/`cardinality` теперь несут дату происхождения (P1-6, ARCHITECTURE §3.2).

### Security

- Контейнер-hardening (C-4, аудит 2026-07-18): продуктовый Dockerfile — non-root
  `USER app` (uid 1000, писабельны только `data/`+`logs/`) + `HEALTHCHECK` на
  `/api/v1/health` (stdlib urllib, без curl); базовые образы обоих Dockerfile
  (python-slim, uv, clickhouse 24.8, superset 4.1.2 — demo тянул `uv:latest`!)
  запинены по digest; все actions в пяти workflow запинены по commit-SHA (`# vX`
  комментарий держит человекочитаемость); CI docker-job теперь ЗАПУСКАЕТ образ
  (smoke: healthy + uid 1000 + user app); release.yml — SLSA-provenance на
  GHCR-образ (`attest-build-provenance`, push-to-registry) и Trivy-скан пушнутого
  образа (fail на HIGH/CRITICAL с доступным фиксом).
- Сканы в CI (C-5, аудит 2026-07-18): (1) job `dependency-audit` — pip-audit по
  залоченному набору зависимостей (`uv export --all-extras` с хэшами, `--require-hashes`;
  локальный прогон: 0 CVE); (2) workflow `codeql.yml` — статанализ python на PR/main/
  еженедельно (паттерн AB_TEST); (3) workflow `gitleaks.yml` — скан секретов на каждый
  push/PR + weekly. Детектор проверен на подсаженных секретах (AWS key + GH PAT → 2 leak);
  полная история репо прогнана локально gitleaks 8.30.1 — 345 коммитов, утечек нет.
  Подавления, если появятся, — только явным `.gitleaks.toml`-allowlist.
- Fingerprint BI-коннекшена при реюзе (C-6, аудит 2026-07-18): `ensure_database` обоих
  адаптеров переиспользует коннекшен ПО ИМЕНИ — стейл-запись (переехавший стенд, другой
  порт/база) молча кормила бы дашборды не тем DWH. Теперь при реюзе host/port(/db) из
  существующей записи (Superset `sqlalchemy_uri` / DataLens `bi/getConnection`) сверяются
  с текущим DWHConfig: mismatch → warning, при `AUTO_BI_BI_CONNECTION_STRICT=true` — отказ.
  Best-effort: нечитаемый fingerprint никогда не ломает билд, только positive-mismatch.
- Лимит одновременных SSE-консьюмеров (C-7, аудит 2026-07-18): `SSEGate` — глобальный cap
  (`AUTO_BI_SSE_MAX_STREAMS`, дефолт 20) и cap на сессию (`AUTO_BI_SSE_MAX_STREAMS_PER_SESSION`,
  дефолт 3; 0 = без лимита); сверх ёмкости `GET .../events` отвечает 429 + `Retry-After`.
  Слот берётся до создания итератора (отбитый консьюмер не съедает билд-события) и
  освобождается в `finally` генератора, включая дисконнект клиента (ARCHITECTURE §3.18).
- Опечатки в `AUTO_BI_*`-переменных больше не молчат (C-2, аудит 2026-07-18): `extra="ignore"`
  у Settings тихо выбрасывал `AUTO_BI_AUTH_ENABLE=true` (auth оставался выключен без следа) —
  `auto_bi serve` на старте сверяет окружение с `Settings.model_fields` (+string-алиасы) и
  пишет warning на каждую неизвестную переменную (`config.warn_unknown_env_settings`).
- Убран дефолт `datalens_password="admin"` (C-8, аудит 2026-07-18): дефолт пустой, signin с
  пустым паролем падает понятной ошибкой «set AUTO_BI_DATALENS_PASSWORD» ДО сетевого вызова
  (симметрично Superset, у которого пароль без дефолта всегда); healthcheck отдаёт её как not-ok.
- `deploy/hf-demo/publish_space.py` — безопасная обвязка (C-3, аудит 2026-07-18): дефолтный
  рабочий каталог = `TemporaryDirectory` с гарантированной очисткой; существующий
  пользовательский путь удаляется ТОЛЬКО при маркере клона этого Space (`.git` + remote
  `huggingface.co/spaces/<SPACE>`) И явном `--force-clean` — чужой каталог не сносится
  (юнит-тесты); токен больше не встраивается в push-URL — аутентификация через inline
  credential helper из env `HF_TOKEN`; новый `--dry-run` (без токена) показывает
  снапшот/дифф и ничего не пушит.
- SQL-guard: denylist табличных функций дополнен (C-1, аудит 2026-07-18) — remote/объектные
  стораджи и lakehouse-форматы (`remoteSecure`, `s3Cluster`/`hdfsCluster`/`urlCluster`/
  `fileCluster`, `azureBlobStorage(+Cluster)`, `gcs`, `oss`, `deltaLake`/`iceberg`/`hudi`
  (+Cluster)) и RBAC-слепые локальные (`merge` — читает все таблицы по regexp правами
  сервисного аккаунта, `dictionary`, `executable`). Закрыта форма AST, где sqlglot парсит
  вызов табличной функции как неквалифицированную таблицу (`dictionary('x')`): таблица без
  схемы с denylist-именем отклоняется, `dm.dictionary` и CTE-алиасы — нет. Заметка о
  кандидате на allowlist — ARCHITECTURE §4.

### Changed

- `explain_high_scan_fraction` предпочитает **живой** знаменатель (P1-6): при доступном
  RunQuery advisor берёт текущий размер таблицы из `system.tables` (кэш на инстанс,
  never-raise) вместо git-замороженного `physical.rows`, который расходится с окружением
  (модель 20M vs демо 1M) и давал ложную долю скана; `evidence.total_rows_source: live|model`
  фиксирует происхождение, фолбэк на модельный `rows` — при недоступном каталоге или live-нуле
  (ARCHITECTURE §3.3).
- Feasibility Advisor судит **effective query**, а не дословный запрос спеки (P1-2):
  фильтры чарта плюс дашбордные контролы, которые реально его сужают (`advisor/effective.py`).
  Контрол засчитывается по тем же условиям, что кодируют оба адаптера — колонка в grain чарта
  и непустой `default`; иначе чарт открывается несуженным и находка честна. До Phase 2
  дашбордные фильтры не компилировались, и чтение одних `query.filters` было верным; с native
  дашборд-фильтрами оно давало ложный `no_filter_on_large_fact` на спеке, где период задан
  только контролом. EXPLAIN тоже считается по effective query — измеряется то, что BI реально
  исполняет (ARCHITECTURE §3.3).
- Advisor запускается во всех entrypoints (P1-2): auto-обзор (`cli build --auto`,
  API `sessions/auto`) и одношаговый `cli build "текст"` раньше шли в сборку мимо него —
  детерминированные находки просто терялись. В путях без диалога вердикты механические
  (`worst_verdicts`, текст самих правил): вердикт по D5 всё равно выносит код, а narration
  стоила бы лишнего вызова провайдера на каждую сборку. `dm_change_request` логируется
  одинаково на всех путях.
- Находка `explain_high_scan_fraction` при многопроходном скане сообщает проходы, а не долю:
  `EXPLAIN ESTIMATE` суммирует все проходы по таблице, поэтому period-compare (текущее окно
  + предыдущее) давал «reads ~146% of dm.sales_daily» — доля выше 100% читается как сломанное
  число. Теперь выше 1 — «29132263 rows … 1.5× its 20000000 rows (more than one pass)»;
  сырое отношение остаётся в `evidence.scan_fraction`.
- Совет этой же находки зависит от формы запроса: скалярному period-compare KPI больше не
  предлагается «сузить период или добавить фильтр» — фильтр у него уже есть, а второй проход
  неустраним (сравнение периодов без него невозможно, и SQL_GEN расширяет внешний скан
  намеренно). Вместо этого — предагрегированный rollup в DM, чтобы сравнение читало бакеты,
  а не сырые строки. Само правило продолжает срабатывать: 29M строк на обновление — реальная
  стоимость, молчание было бы ложным негативом. Признак — `evidence.period_compare`.
- Дефолтная модель GraceKelly — `claude-sonnet-5` (было `claude-sonnet-4-6`).

- Запрос своими словами без имени витрины (X-3): поле `synonyms` у таблиц и колонок
  семантической модели — рукописные альтернативные имена («удержание»/«retention»
  у когортной витрины). Синонимы рендерятся в LLM-промпты и скорятся контекст-селекцией
  с весом имени, так что «Покажи удержание клиентов» находит `dm.cohort_retention`
  на любом размере DM. Плюс когортный паттерн в правилах PROPOSE: при наличии
  когортной витрины (форма: время × периодов-с-первого-события × число субъектов)
  дашборд собирается из неё — heatmap-треугольник, bar раннего удержания,
  big_number размера базы.


- **P1-3 Anthropic is a core dependency:** `anthropic` SDK ships with plain
  `pip install autobi-agent` and the production Docker image (default
  `AUTO_BI_LLM_PROVIDER=anthropic`). Optional extra `[anthropic]` kept as a
  no-op alias for older install lines. USER_GUIDE / DEPLOYMENT / ARCHITECTURE
  updated.


- **Дефолтная модель прямого Anthropic-провайдера — `claude-sonnet-5`** (было
  `claude-sonnet-4-6`; аудит 2026-07-18, D-7). Запрос уже строился совместимо: adaptive
  thinking на reasoning-шагах и `disabled` на механических передаются явно на каждом
  вызове, sampling-параметров и `budget_tokens` в запросе нет, thinking-блоки отбрасываются
  при разборе ответа. `anthropic_max_tokens` осознанно остался 16000: вызов
  без стриминга, а SDK отклоняет нестримингованный запрос, чья оценка длительности выходит за
  HTTP-таймаут. Токенизатор новой модели считает тот же текст примерно на 30% дороже —
  прежние оценки бюджета и стоимости пересчитать, а не переносить.
- **Прайс-таблица бюджета покрывает актуальную линейку** (D-7): Opus 4.8 / Sonnet 5 /
  Sonnet 4.6 / Haiku 4.5, USD за 1000 токенов, дефолт `Settings.llm_budget_prices` и
  `.env.example`. Незалистанная модель тарифицируется в 0, поэтому её нужно добавить
  прежде, чем полагаться на лимит стоимости. У Sonnet 5 действует пониженный вводный
  тариф до 2026-08-31 — в таблице стандартный, чтобы гейт скорее переоценивал трату,
  чем недооценивал.

### Removed

- Рабочая кухня вынесена во внутренний репозиторий (аудит 2026-07-18, п.5): 16 файлов
  `docs/history/`, 22 файла `docs/plans/` и пооперационный лог сессий из `CLAUDE.md`.
  Это разборы стендов, машин и ходов отладки — они устаревали быстрее, чем окупалась
  их правка. Поддерживаемые доки (`README`, `ARCHITECTURE`, `PLAN`, `USER_GUIDE`,
  `DEPLOYMENT`, `ONBOARDING_DWH`, `MARKET`, `CHANGELOG`) не тронуты; ссылки на
  вынесенное помечены префиксом `internal/` (конвенция описана в README §Документация).
- Заимствованная витрина-срез из репозитория убрана целиком (аудит 2026-07-18, п.6):
  `semantic/model_x5.yaml`, `docs/gaps_report_marts_x5.md`, стендовые
  `scripts/stand_create_marts.sql` / `scripts/stand_load_bv_mat.sh` и runbook 1.10.
  Продуктового кода не касается: модель ничем не загружалась (golden-сьюты идут на
  `model.yaml` / `model_gp.yaml`), в wheel `semantic/` и не входил.

### Fixed

- **P0-2 BI artifact collisions:** technical dataset (and DataLens entry) names
  include a build/session namespace fingerprint (`adapters/artifacts.py`);
  `compile_and_build` pins it via `set_artifact_namespace` before `build()`.
  Two sessions with the same title/chart ids no longer share or PUT-overwrite
  one virtual dataset; rebuilds get a fresh token so an older dashboard keeps
  its SQL. `BIAdapter` Protocol unchanged (optional concrete helper).
- **P0-3 resource bounds:** fail-closed remote bind (`ALLOW_INSECURE_REMOTE` /
  auth / demo profile required for non-loopback); `MAX_CONCURRENT_BUILDS`
  (default 2) → 503 + Retry-After on approve; work quota on auto/approve/insights
  (forced when `demo_auto_only`). LLM session quota stays separate (O-2).
- **P1-4 DCR / observability RBAC:** list/detail DCR scoped by session owner +
  allowed schemas (foreign → 404); PATCH status is admin-only (own non-admin →
  403). Global `GET /observability/llm` is admin-only; analysts get own-session
  spend only. No cross-user `session_request` leak.
- **P2-1 Anthropic usage:** successful `stop_reason` (`end_turn`/…) stored as
  `status=completed`; usage summary also counts legacy `end_turn` rows as ok.
- **P1-3 install hint:** missing-SDK error names `autobi-agent[anthropic]`
  (not the rejected `auto-bi` distribution).
- **P2-2 seed join-field false drop:** `seed_analysis` keeps already-qualified
  column refs (`dm.stores.city`); only bare columns are prefixed with
  `query.table` — join fields used in the spec are no longer reported as
  «не вошли в дашборд».
- **P1-5 strict models:** IR, semantic model, seed, grounding report, API
  request bodies use `extra="forbid"` (`StrictModel`); typos like `limt` /
  `max_chart` / `tablse` fail validation instead of silently defaulting.
  `AutoSessionRequest.max_charts` bounded to 1..12; request/reply length caps.
- **P1-1 auto-overview period:** период «last 12 months» запекается в SQL WHERE
  каждого чарта обзора (KPI/бары/таблица), а не только в native time-control
  (он покрывал в основном линию динамики). SQL_GEN понимает relative-токены
  `last N days|months|years` как GTE-bound. Серия `yoy_pct` остаётся на полной
  истории (лагу нужны prior-year точки).
- **P0-1 raw_sql:** LLM text/fields/patch больше не может эмитить `raw_sql`
  (schema без поля + `validate_spec(allow_raw_sql=False)`); schema-RBAC ходит
  по AST SELECT, а не только по `query.table`-метке; `guard_sql` режет remote
  table-functions; DataLens+raw явно rejected. CLI `auto_bi raw` без изменений.
- Superset: bar по временнОй оси (когорты по месяцам) держит time-ось с датами
  вместо категориальной с сырыми epoch-ms; числовые категории (`store_id`)
  по-прежнему форсятся категориальными.
- Superset: heatmap больше не метит нулевой ряд ordinal-оси (`months_since=0`)
  как `<NULL>` и не тасует числовые метки алфавитно (upstream #33105/#31318):
  числовая Y-ось малой кардинальности zero-pad'ится adhoc-колонкой
  (`LPAD(CAST(...))`, «00»…«23») — метка нуля и порядок чинятся разом;
  id-подобные оси сохраняют естественные подписи.

## [0.3.1] - 2026-07-10

Первый релиз на PyPI: `pip install autobi-agent`.

### Changed

- **Имя дистрибутива — `autobi-agent`** (было `auto-bi`; импортируемый пакет и консольная
  команда остаются `auto_bi`). PyPI отвергает `auto-bi` как «too similar to an existing
  project»: при сравнении имён разделители схлопываются, поэтому имя конфликтует с уже
  занятым пакетом-заглушкой `autobi`. Trusted publisher зарегистрирован под новым именем.

## [0.3.0] - 2026-07-10

Session-resume, публичное демо (живой Space + режим `AUTO_BI_DEMO_AUTO_ONLY`),
PyPI-конвейер, единый формат KPI-ряда и DataLens-доводки (пресет периода,
RU-единицы, percent-ось).

### Added

- Session-resume после рестарта (X-4): промах in-memory реестра лениво регидрирует
  сессию из Store (schema v7: `sessions.owner/target_bi/pinned`) — фаза диалога,
  текущий spec, счётчик clarify-раундов, статус билда и абсолютная ссылка на дашборд
  переживают рестарт `auto_bi serve` и eviction за `MAX_SESSIONS`; билд, оборванный
  рестартом, воскресает как `failed` и пересобирается повторным approve. RBAC
  сохраняется (владелец и скоуп схем восстанавливаются; legacy-сессии без владельца
  при включённом auth видит только админ). `DELETE /sessions/{id}` теперь ставит
  tombstone (`status='deleted'`), иначе гидрация воскрешала бы удалённую сессию.
- Живое публичное демо: <https://juliome20-auto-bi-demo.hf.space> (Hugging Face
  Space, один контейнер ClickHouse + Superset + Auto_BI + nginx) — анонимный
  auto-overview до готового дашборда в Superset без логина.
- Режим публичного демо (P8): `AUTO_BI_DEMO_AUTO_ONLY=true` открывает только
  детерминированный авто-обзор — text/fields-сессии, правки словами и enrichment
  отвечают 403 (человеческим сообщением), LLM-провайдер не подключается вовсе
  (`DisabledLLM`), UI дизейблит вкладки по флагу в `/health`;
  `AUTO_BI_SUPERSET_PUBLIC_URL` разводит публичную базу ссылок на дашборд и
  внутренний Superset-URL адаптера. Упаковка демо — `deploy/hf-demo/`
  (один контейнер CH+Superset+auto_bi+nginx для HF Space; фронт переведён на
  относительные пути и работает за префиксом `/agent/`), smoke — workflow
  `demo-image.yml`.
- Прокси-wiring per-IP квот (F-2, предпосылка публичного деплоя): `auto_bi serve`
  явно включает uvicorn `proxy_headers` и принимает `AUTO_BI_FORWARDED_ALLOW_IPS`
  (каким прокси доверять `X-Forwarded-For`) — за reverse-proxy login-лимитер и
  LLM-квота считают реальные клиентские IP, а не один общий bucket адреса прокси;
  nginx-пример и compose-схема в DEPLOYMENT дополнены заголовками и гочами.
- Лимитер чистит неактивные ключи (L-4): записи, молчащие дольше
  `max(окно, потолок lockout)`, вычищаются амортизированным сканом — память процесса
  не растёт с каждым уникальным IP при публичной экспозиции; сброс эскалации при этом
  не дешевле, чем отсидеть максимальный lockout.
- Release-конвейер: job `pypi` (X-2) — публикация sdist+wheel на PyPI через Trusted
  Publishing (OIDC, без токена в секретах) на каждый тег `vX.Y.Z`; метаданные пакета
  (license/authors/classifiers/urls) дополнены в `pyproject.toml`, дистрибутив проверен
  `twine check` и установкой в чистое окружение. Первая публикация требует одноразовой
  настройки trusted publisher владельцем (инструкция — комментарий в `release.yml`).
- DataLens: пресет периода на дашборд-селекторе (B5) — `DashboardFilter.default`
  («last 12 months» / ISO-диапазон) компилируется в relative-interval токен
  (`__interval___relative_-12M___relative_+0d`) в `defaultValue`+`defaults` контрола;
  реально сужает ДАННЫЕ чартов в скоупе при открытии, не только бейдж
  (контракт-тест подтверждает сужение через `/api/run` c тем же params-токеном).
- DataLens: русские единицы величин (N2) — крупный рублёвый/счётный KPI рендерится как
  «236 млрд ₽» (замер магнитуды inline-`/api/run`-пробой → масштабирование меры в
  сабселекте датасета → RU-единица postfix'ом, шрифт KPI «s» чтобы влезало), а ось
  значений line/bar/area — тики «8…16» с подписью оси «млрд ₽» вместо SI «8B…16B»
  (зеркало Superset `ru_kpi_scale`/`_axis_scale`).

### Fixed

- Web-UI: ссылка «Дашборд готов» теперь абсолютная (F-1) — адаптеры возвращают
  BI-относительный url, и относительный href резолвился против хоста Auto_BI
  (`:8200` вместо `:8088`) → клик давал 404 при раздельных хостах. Сервер склеивает
  настроенную базу BI (`AUTO_BI_SUPERSET_URL`/`AUTO_BI_DATALENS_URL`) в `dashboard_url`
  и SSE-событие `done` (той же конвенцией, что CLI).
- RU-масштаб оси значений применяется только к одномерным чартам (F-3) — делитель
  тарифицировался по первой мере, но делил ВСЕ метрики чарта, так что на line
  «выручка + число заказов» вторая мера рендерилась в чужих единицах (млрд ₽).
  Мультимерные чарты сохраняют компактный SI-формат (оба адаптера).
- KPI-заголовок в полосе 1–10 единиц масштаба держит один знак после запятой (L-1):
  целочисленное округление теряло до трети величины («1,5 млрд» → «2 млрд»).
  Оба адаптера (Superset `",.1f"`, DataLens `precision: 1`).
- OpenAPI/docs-страница отдаёт версию пакета вместо захардкоженной «0.1.0» (L-3).
- Публичное демо: пересборка дашборда после первого билда падала с 422
  «A database with the same name already exists» — у Public-роли (Gamma-like)
  оставался `can_read on Database`, из-за чего FAB (is_item_public ДО verify_jwt)
  исполнял даже аутентифицированный GET /api/v1/database/ адаптера как анонимный,
  а анонимному DatabaseFilter прячет коннекшены → get-or-create всегда шёл в create.
  `superset_public_role.py` теперь снимает и это чтение; smoke `demo-image.yml`
  гоняет ДВА билда в одном контейнере, чтобы lookup-путь не оставался слепым пятном.
- Демо-режим: `PATCH /api/v1/dm-change-requests/{id}` закрыт 403-гейтом как и
  остальные записи разделяемого состояния (в демо DCR не создаются — defense in depth).
- Superset: KPI-плитки приведены к одному формату и отцентрированы по обеим осям.
  Процентная плитка рендерила «1.5%» одной строкой — длинная строка ужимала кегль,
  а без строки юнита плитка центрировалась иначе соседних; теперь она зеркалит
  плитки с юнитами («1.5» + «%» строкой ниже: метрика ×100 в SQL, формат `.1~f`
  с тримом хвостового нуля). Пропорции шрифтов value/unit запинены на всех
  плитках, центрирование — dashboard-CSS (`KPI_CENTER_CSS`, детерминированный
  шов нативного формата, как position_json).
- DataLens: percent-ось (C1) — доля/ratio-чарты показывали сырые 0..1 на оси значений
  (3 сессии считалось engine-limit). Причина: ось читает `formatting` поля ТОЛЬКО при
  `placeholder.settings.axisFormatMode="by-field"` (реверс datalens-ui 0.3831.0), флаг
  не выставлялся. Плюс горизонтальный bar требует wizard-конвенцию id плейсхолдеров
  (dimension→`y`, measures→`x` при том же позиционном порядке) — иначе формат оси
  ищется на категорийной оси и теряется.

## [0.2.0] - 2026-07-06

Первый версионированный релиз. До него проект жил без тегов/CHANGELOG/GHCR-публикации
(`version = "0.1.0"` с основания репозитория) — этот релиз фиксирует накопленный функционал
Phase 0–4 и последующего hardening-трека и вводит сам процесс релизов.

### Added

- **Сборка дашборда из естественного языка** (текст → уточнения по расхождениям с
  DM → `DashboardSpec` (IR) → сборка) поверх ClickHouse/Greenplum, с engine-aware
  **Feasibility Advisor** (детерминированные вердикты `ok`/`spec_adjustment`/`dm_change_request`).
- **Fields-first режим** (drag&drop полей витрины) и **auto-overview** (курируемый
  дашборд по одной витрине без LLM) — второй и третий вход в тот же пайплайн.
- Аналитическое ядро IR: ratio-меры, произвольный `time_grain`, `yoy`/`pop`/лаг-N,
  `running_share` (Pareto/ABC), `histogram`.
- Два движка DWH (ClickHouse, Greenplum/Greengage) и два BI-адаптера (Apache Superset,
  Yandex DataLens self-hosted) за одним BI-агностичным IR.
- Web UI: чат, превью спецификации, вердикты advisor'а, режим итераций (патч-правки
  словами), заявки владельцу DM (`dm_change_request`), панель наблюдаемости (токены/
  латентность по шагам агента), панель «Что видно» (детерминированные инсайты без LLM).
- Прямой Anthropic Messages API как дефолтный LLM-провайдер (`ANTHROPIC_API_KEY`);
  GraceKelly — документированная опция.
- Auth/RBAC по схемам DWH (opt-in), с security-hardening: secure-cookie, rate-limit на
  login с растущим бэкоффом, токены хранятся как sha256-хэш, периодический purge.
- Ops-hardening: `GET /api/v1/ready` (store + DWH + BI healthcheck), структурные логи
  (`--log-format json`), устойчивая запись оборванных билдов после рестарта процесса.
- CI: офлайн-сьют (ruff/black/mypy/pytest/advisor-eval) + отдельный `integration`-job,
  поднимающий живой ClickHouse+Superset стенд в GitHub Actions на каждый push/PR.
- `docs/DEPLOYMENT.md` — гайд по продакшен-развёртыванию (reverse-proxy/TLS, бэкап
  SQLite, ротация логов, чеклист секретов).
- Релизный конвейер (этот релиз): `docker build` на каждый PR (job `docker` в
  `ci.yml`), публикация образа в GHCR по тегу `vX.Y.Z` (`.github/workflows/release.yml`),
  `auto_bi --version`, поле `version` в `/api/v1/health`, coverage-бейдж, генерируемый CI.
- **Аналитическое ядро в text-first** (S01): ratio/`time_grain`/`yoy`/`pop`/лаг-N/
  `running_share` (Парето)/`histogram` теперь запрашиваются и подтверждаются словами, а не
  только через fields-first spec — `SPEC_RULES` документирует каждый примитив few-shot
  JSON-примерами дословно согласованными с `ir/validate.py`; grounding распознаёт
  производные ratio-меры (обе составляющие есть в модели → matched) и держит аналитические
  обороты («год к году», «Парето», «нарастающим итогом», «распределение») как форму подачи,
  не отдельные сущности. Golden-eval расширен 16 новыми кейсами на сами примитивы
  (`g13`–`g23` + `it4` на CH, `gp_g9`–`gp_g12` на GP).
- **Скалярный period-compare KPI** (`Measure.compare`): плитка `big_number`, значение
  которой = одно число — последний период vs год/период назад (`yoy`/`pop`), как процент
  или абсолютная дельта. Считается условной агрегацией по двум бакетам (без окна) → форма
  скаляра сохраняется; авто-обзор при ≥2 годах истории добавляет плитку «‹мера›, г/г»
  рядом с уровнем главной меры. CH live-verified.
- `auto_bi eval --llm-mode {live,replay,record}` — record/replay-фикстуры для вызовов LLM
  в golden-сьюте (`auto_bi/llm/fixture.py`): сьют можно перегонять офлайн по ранее записанным
  ответам вместо живого провайдера. Фикстуры записаны для всего текущего сьюта (37 CH + 16 GP
  кейсов, 53 файла в `tests/fixtures/golden_llm/`) и подключены как офлайн шаг CI
  (`quality`-job, сразу после advisor-сьюта) — golden-регрессии ловятся на каждый push/PR без
  сети и секретов.
- **Per-IP/per-day лимит на LLM-сессии** (`POST /api/v1/sessions` и `/sessions/{id}/reply`):
  защита LLM-бюджета от неконтролируемого расхода перед публичным демо, тот же
  sliding-window+lockout механизм, что и `LoginRateLimiter`, конфигурируемый
  (`AUTO_BI_SESSION_RATE_ENABLED`/`AUTO_BI_SESSION_RATE_PER_DAY`, выключено по умолчанию —
  без изменений в локальном/dev-поведении) — 429 + `Retry-After`. `POST /sessions/auto`
  (детерминированный auto-overview, без LLM) не гейтуется.
- **Преднастроенный период дашборда (Superset, B5)**: `DashboardFilter.default`
  теперь наполняет `defaultDataMask` — дашборд открывается уже суженным на период
  («Last quarter») или значение фильтра, а не с пустым выбором.

### Changed

- **Читаемые KPI, оси и легенды (Superset)**: крупный рублёвый `big_number` показывается
  как «236 / млрд ₽» (масштаб + русская единица отдельной строкой) вместо d3 SI «236G»;
  **оси line/bar/area** тоже показывают русские единицы («15 … млрд ₽» на заголовке оси
  значений) вместо «15G»; легенды/тултипы/шапки колонок читают человеческое имя меры
  («Выручка») вместо сырого SQL-алиаса («sum_revenue»). SQL и значения не меняются — только
  отображение (percent-оси и средние не масштабируются).
- **Авто-обзор открывается на последних 12 месяцах**: детерминированный auto-overview теперь
  ставит преднастроенный период (`last 12 months`) на дашборд-фильтр времени — свежие данные,
  но полный год в кадре для yoy-KPI; пользователь расширяет до всей истории на дашборде.
  Раньше открывался на всю историю.
- **Человеческие легенды теперь и в DataLens**: поле датасета показывает человеческое имя меры
  («Выручка») в легенде/подписи оси, а не сырой SQL-алиас («sum_revenue») — как в Superset. Имя
  берётся из `measure.label` или короткой формы описания колонки модели; привязка чарта к полю
  идёт по алиасу-источнику, поэтому меняется только отображаемый заголовок.

### Fixed

- **Преднастроенный период реально сужает данные (Superset)**: дашборд-фильтр времени доносил
  `time_range` до чарта, но ECharts-timeseries-запрос не называл временную колонку, поэтому
  Superset не применял диапазон — ряд оставался полным, хотя контрол показывал период и бейдж
  «Applied filters (1)». Timeseries-чарт над временной колонкой теперь ставит `granularity_sqla`,
  и диапазон действительно ре-скоупит ряд (свежая сборка: 24 месяца → 11 под пресетом «last 12
  months»). Контракт-тест теперь проверяет само сужение, а не только round-trip конфигурации.
- **Сортировка баров по мере (Superset)**: структурные бары снова сортируются по значению
  (крупнейший сверху), а не алфавитно. Гуманизация легенды рассинхронила ключ `x_axis_sort`
  (ссылался на алиас, а Superset матчит по метке метрики); плюс для горизонтальных баров
  направление сортировки инвертируется, чтобы крупнейший был сверху, а не снизу.
- **Ровный правый край раскладки** (auto-overview): чарт, оставшийся один в последнем ряду
  (после отсечения детальной таблицы по `max_charts`), растягивается на всю ширину — нет
  «рваного» полупустого ряда.

[Unreleased]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/brownjuly2003-code/Auto_BI/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/brownjuly2003-code/Auto_BI/releases/tag/v0.2.0
