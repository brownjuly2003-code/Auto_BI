# Auto_BI — руководство пользователя

Как пользоваться Auto_BI: превратить запрос (текстом или раскладкой полей витрин) в
дашборд в выбранной BI. Здесь — практика; архитектура и «почему так» — в
[ARCHITECTURE.md](ARCHITECTURE.md), подключение нового DWH — в [ONBOARDING_DWH.md](ONBOARDING_DWH.md).

---

## 1. Что нужно до начала

- **Python ≥ 3.12** и установленный пакет: `pip install -e .` в корне репозитория
  (даёт консольную команду `auto_bi`).
- **Семантическая модель** витрин — файл `semantic/model.yaml`. Если его нет, сначала
  сделайте интроспекцию DWH (см. [ONBOARDING_DWH.md](ONBOARDING_DWH.md) или быстрый старт ниже).
- **DWH** (ClickHouse — v1) с read-only ролью, доступный с машины, где запускается Auto_BI.
- **BI** — Apache Superset (v1) или self-hosted Yandex DataLens (v2).
- **LLM** — по умолчанию прямой **Anthropic API** (`ANTHROPIC_API_KEY`); опционально —
  прямой **Mistral API** (`AUTO_BI_LLM_PROVIDER=mistral`, `MISTRAL_API_KEY`) или локальный
  сервис **GraceKelly** (`AUTO_BI_LLM_PROVIDER=gracekelly`, §6). Нужен для
  диалога/предложения spec'а; детерминированные шаги (валидация, advisor, сборка) от него не зависят.

Все секреты и адреса — через переменные окружения с префиксом `AUTO_BI_` или файл `.env`
в корне (см. §6). Секреты в коде/доках не хранятся.

---

## 2. Быстрый старт (ClickHouse + Superset)

```bash
# 1. установка (Anthropic SDK — core-зависимость, default LLM-провайдер)
pip install -e .

# 2. настройка доступа (пример .env — значения свои)
#    AUTO_BI_CH_HOST=localhost   AUTO_BI_CH_USER=auto_bi_ro   AUTO_BI_CH_PASSWORD=...
#    AUTO_BI_CH_DATABASE=dm
#    AUTO_BI_SUPERSET_URL=http://localhost:8088   AUTO_BI_SUPERSET_PASSWORD=...
#    ANTHROPIC_API_KEY=sk-ant-...
#    (или AUTO_BI_LLM_PROVIDER=mistral + MISTRAL_API_KEY;
#     или AUTO_BI_LLM_PROVIDER=gracekelly +
#     AUTO_BI_GRACEKELLY_URL=http://127.0.0.1:8011 — локальный сервис-опция, см. §6)

# 3. интроспекция DWH -> черновик модели
auto_bi introspect --output semantic/model.yaml

# 4. (опц.) что в модели «слепо» и стоит уточнить
auto_bi gaps --offline

# 5а. одношаговая сборка из текста
auto_bi build "Выручка по магазинам за июнь 2026, топ-10"

# 5б. или диалог с уточнениями и подтверждением
auto_bi chat

# 5в. или web UI (текст + drag&drop полей)
auto_bi serve            # http://127.0.0.1:8200
```

Команда печатает ссылку на готовый дашборд.

---

## 3. Команды CLI

Запуск: `auto_bi <команда> [опции]`. Везде `--model-path` по умолчанию `semantic/model.yaml`.
`auto_bi --version` печатает версию пакета и выходит (совпадает с полем `version` в
`GET /api/v1/health`).

| Команда | Назначение |
|---|---|
| `build "<запрос>"` | Happy-path: текст → spec → сборка дашборда, без диалога. `--target superset\|datalens` (по умолчанию `superset`). |
| `build --auto <таблица>` | **Авто-обзор:** курируемый обзорный дашборд из витрины, без текста и без LLM (детерминированно). `--max-charts N` (def 8), `--target`. |
| `chat` | Диалог: уточнения → превью spec → `да` собрать / правка словами / `отмена`. После сборки можно дорабатывать словами (итерации). |
| `serve` | HTTP API + web UI (FastAPI/uvicorn). `--host` (def `127.0.0.1`), `--port` (def `8200`). |
| `introspect` | Интроспекция DWH → черновик `model.yaml`. `--engine clickhouse\|greenplum` (def `clickhouse`; GP использует `AUTO_BI_GP_*`), `--database`, `--output`. |
| `gaps` | Детерминированный отчёт «что в модели не заполнено / неоднозначно». `--offline` без подключения к DWH, `--output file.md` в файл. |
| `eval` | Прогон eval-сьютов. `--suite advisor\|golden\|all`, `--cases id1,id2` (подмножество). advisor — офлайн; golden — `--llm-mode live` (деф., живой LLM) \| `replay` (офлайн по записанным фикстурам) \| `record` (живой LLM + запись фикстур), `--fixtures-dir DIR` (деф. `tests/fixtures/golden_llm`). |
| `dbt-import` | Обогащение `model.yaml` из dbt-артефактов (описания, связи). `--manifest` (обяз.), `--catalog`, `--dry-run`. Заполняет ТОЛЬКО пустые значения — ручные правки всегда выигрывают. |
| `prune` | Удаление BI-артефактов прошлых ревизий через ownership-леджер (последняя сборка каждой сессии всегда переживает). `--session ID` (только одна сессия), `--dry-run` (показать кандидатов, не удаляя). |
| `decision validate` | **DecisionOps.** Offline-проверка decision products против metric contracts (пины версий, lifecycle, dimensions, policy). Аргументы — файлы или каталоги контрактов `.yaml`/`.yml`/`.json`, `--json` для вывода отчёта. Каждый найденный в каталоге (и вложенных) файл с таким расширением проверяется как контракт: посторонний JSON блокирует (`contract.unknown_kind`) — держите его вне переданных каталогов. Файл, до которого доходят несколько аргументов (каталог и файл в нём, другое написание пути, ссылка), читается один раз; копия файла в другом каталоге — второй файл, и повтор id блокирует (`contract.duplicate_id`). Ни DWH, ни BI, ни LLM. |
| `decision verify` | **DecisionOps.** Полный Analytics Release & Evidence Gate для ОДНОГО продукта: `--base`/`--candidate` (каталоги с `model.yaml`), `--evidence DIR`, `--dashboards DIR`, `--assets PATH`, `--results FILE`, `--product ID`, `--environment REF`, `--created-at TS`, opt-in `--signing-key PRIVATE_PEM`, `--json`. Тоже полностью offline без `--dwh`. |
| `decision evidence` | **DecisionOps.** Чтение написанного evidence bundle с пересчётом всех content address. Аргумент — каталог evidence или один run-каталог внутри; `--run ID` (если run'ов несколько), `--artifact ID` (напечатать один payload), `--expect-digest sha256:…`, повторяемый `--trust-key PUBLIC_PEM`, `--json`. Signed bundle без совпавшего trust key блокируется. |
| `decision runs` | **DecisionOps.** Локальный каталог прогонов: `decision runs EVIDENCE_ROOT [--json]`. Читает manifests без проверки payload hashes и подписи; `integrity_checked=false`. Для проверки выбранного bundle используйте `decision evidence --run ID`. |

### build
```bash
auto_bi build "Динамика выручки по дням за Q2 2026"
auto_bi build "Топ-10 городов по выручке" --target datalens
```
Запрос проходит grounding по модели → PROPOSE_SPEC (LLM) → валидация spec и SQL
(sqlglot + EXPLAIN/LIMIT-прогон на живом DWH) → детерминированная сборка адаптером.
Если модель не найдена — подсказывает запустить `introspect`.

#### Авто-обзор (`--auto`)
```bash
auto_bi build --auto dm.sales_daily
auto_bi build --auto dm.sales_daily --max-charts 6 --target datalens
```
Третий вход в пайплайн рядом с текстом и fields-first, **детерминированный, без LLM.**
Из ролей колонок и кардинальности витрины собирается курируемый обзорный дашборд (не «все
возможные графики»): KPI по каждой мере (+ скалярный KPI «г/г» для главной меры при ≥2 годах
истории — последний период vs год назад) → динамика по времени → топ-N разрезы (включая
атрибуты смежных таблиц через JOIN: город / категория / формат) → доля (part-to-whole баром,
без pie) → детализация.
Перед сборкой печатается секция **Advisor** — вердикты по проблемным чартам (пусто, если
всё чисто). Текст механический, самих правил: вердикт выносит код, а LLM здесь нет и его
формулировка не нужна. Advisory-only — сборка идёт в любом случае.
Дашборд несёт интерактивный **фильтр периода** и открывается на **последних 12 месяцах**
(свежие данные, но полный год в кадре для сравнения год-к-году) — период расширяется до всей
истории прямо на дашборде. Для Superset это нативный time-фильтр: он реально сужает графики с
временной осью (KPI-«итог» без даты остаётся полным числом — по построению). Легенды, подписи
осей и заголовки колонок читают **человеческие имена** мер («Выручка»), а не SQL-алиасы; крупные
рублёвые KPI/оси показываются как «236 млрд ₽» (в DataLens единицы — SI «236B», ограничение движка).
Агрегируются только меры (или COUNT, если их нет), id-разрезы заменяются именами, JOIN —
только по связям модели. Дальше — тот же путь валидации/SQL/сборки, что и у `build`.

### chat
Интерактивный REPL. Слова-подтверждения: `да / ок / строй / собирай / build / yes / +`.
Выход: `выход / quit / exit / q`. После сборки дашборда правка словами доработает spec и
пересоберёт; неудачная правка не теряет сессию — дашборд остаётся прежним.

### eval
```bash
auto_bi eval --suite advisor                     # офлайн, без LLM/DWH
auto_bi eval --suite golden                       # через живой LLM (Anthropic по умолчанию)
auto_bi eval --suite all --cases g1,g12_revenue_by_city_join
auto_bi eval --suite golden --llm-mode replay --fixtures-dir tests/fixtures/golden_llm   # офлайн, без провайдера/ключа
auto_bi eval --suite golden --llm-mode record --fixtures-dir tests/fixtures/golden_llm   # живой LLM + запись фикстур
```
Движок берётся из модели (`physical.engine`), сьюты выбираются под него (CH-набор vs GP-набор).
Прогонять перед любым изменением промптов.

`--llm-mode` управляет только golden-сьютом (advisor всегда офлайн). `record` гоняет настроенный
провайдер (`AUTO_BI_LLM_PROVIDER`) и на каждый кейс пишет `<fixtures_dir>/<case_id>.json` —
последовательность вызовов `complete()`, которые сделал кейс (grounding → propose_spec →
опционально patch). `replay` читает эти файлы и вообще не создаёт LLM-клиент — можно гонять
golden-сьют в CI/локально без ключа и без сетевых вызовов. Если код/промпт с момента записи
фикстуры стал звать LLM иначе (другой шаг/схема, лишний или недостающий вызов) — `replay`
падает явной ошибкой на этом кейсе, а не тихо подставляет чужой ответ; фикстуры этого кейса
нужно перезаписать `--llm-mode record`. См. ARCHITECTURE §3.14.

### prune
```bash
auto_bi prune --dry-run           # показать кандидатов, ничего не удаляя
auto_bi prune                     # удалить артефакты прошлых ревизий всех сессий
auto_bi prune --session <id>      # только одна сессия
```
Ручная чистка BI-артефактов **прошлых ревизий** через ownership-леджер. Кандидаты выбираются
по владению (session/build_token), **никогда по имени/заголовку**: живые строки билдов, которые
уже не являются последней сборкой своей сессии. Последний дашборд каждой сессии всегда
переживает прунинг — удаляются только замещённые ревизии, не чужие текущие дашборды; общий
`database`-коннекшн не трогается. Удаление идёт по native id в порядке чарт → дашборд → датасет.
Кросс-target rebuild (сессия строила в Superset, затем пересобрана `--target datalens`)
тоже прунит ревизию прежнего таргета: rebuild = замена дашборда сессии, а не второй
параллельный дашборд. Обычно чистить руками не нужно: при ребилде это делается автоматически (см.
`AUTO_BI_PRUNE_ON_REBUILD` в §6) — команда для операторов, оставивших авто-прунинг выключенным
или желающих убрать накопившиеся сироты разом. `--dry-run` печатает кандидатов и ничего не
удаляет. Недоступный BI-таргет → его строки пропускаются, строка с неудавшимся удалением
остаётся `live` и повторяется следующим прунингом; код возврата 0 при чистом прогоне, 1 если
что-то не удалось или было пропущено. См. ARCHITECTURE §3.17.

### decision
```bash
auto_bi decision validate contracts/                     # продукты ↔ metric contracts
auto_bi decision verify contracts/ \
        --base semantic/base --candidate semantic/head \
        --evidence .evidence --dashboards dashboards/ \
        --assets assets/ --results results.yaml \
        --signing-key /secure/path/evidence-private.pem
auto_bi decision runs .evidence --json                   # выбрать run перед проверкой
auto_bi decision evidence .evidence \
        --trust-key /secure/path/evidence-public.pem     # digest + signature
auto_bi decision evidence .evidence --artifact check-semantic.diff \
        --trust-key /secure/path/evidence-public.pem
```
Контур DecisionOps: не «запрос → дашборд», а проверка, что изменение семантики не ломает
governed-числа. `validate` отвечает на один вопрос — согласованы ли контракты между собой.
`verify` прогоняет полную последовательность гейта: валидация контрактов → заморозка двух
semantic snapshot'ов → семантические gap'ы → diff → impact до продукта, дашборда и владельца →
инвалидация verified assets → сверка результатов → компиляция дашбордов под их BI **без единого
remote-вызова** → сборка content-addressed evidence bundle и его повторная верификация.

Чтобы найти нужный прогон среди нескольких, выполните `auto_bi decision runs .evidence --json`.
Команда читает только manifests непосредственных run-каталогов и сортирует строки по имени
каталога, не по времени. Она ничего не записывает. Поля строки:

| Поле | Как использовать |
|---|---|
| `run_id` | Имя каталога; передайте его в `decision evidence --run`. |
| `manifest_run_id` | ID, записанный внутри manifest; после переименования каталога может отличаться от `run_id`. |
| `product_ref`, `created_at` | Заявленные в manifest продукт и время; помогают выбрать прогон. |
| `manifest_digest` | SHA-256 прочитанных байтов manifest; передайте целиком, включая `sha256:`, в `--expect-digest`. |
| `integrity_checked` | Всегда `false`: каталог не проверяет payload hashes или подпись, даже у signed bundle. |

Скопируйте `run_id` и `manifest_digest` из одной выбранной строки, заменив заполнители
в команде ниже. Для signed bundle добавьте `--trust-key` с доверенным public key,
полученным независимо от bundle:

```text
auto_bi decision evidence .evidence --run "<run_id>" --expect-digest "<manifest_digest>" --json
```

Эта команда проверяет выбранный bundle, включая совпадение двух run ID. Digest из каталога
фиксирует увиденную версию manifest, но сам по себе не доказывает её подлинность или
одобрение владельцем. Если manifest изменился между командами, несовпадение pin даёт
exit `4` и отчёт с `evidence.digest_unexpected` в stdout. При чтении конкретного payload
через `--artifact ID` несовпадение pin также даёт exit `4`, но stdout пуст, причина — в stderr.
Pin не заменяет проверку payload hashes и подписи и не создаёт атомарный snapshot каталога.

Чтобы прочитать owner-summary signed bundle, используйте те же выбранные значения
и путь к доверенному ключу (замените все заполнители):

```text
auto_bi decision evidence .evidence --run "<run_id>" --expect-digest "<manifest_digest>" --artifact report-summary-md --trust-key "<public-key.pem>"
```

`--artifact` проверяет digest выбранного payload и ограничения его чтения; остальные
payload этой командой не проверяются. Для полной проверки сначала выполните inspection
без `--artifact`, добавив тот же `--trust-key`.

Пустой evidence root при `--json` возвращает `[]` с exit `0`; текстовый режим печатает
только заголовок каталога. Каталоги без manifest пропускаются.
Отсутствующий или недоступный root, невалидный либо нечитаемый manifest, каталог вместо
manifest и перенаправленный sealed run дают exit `2`, stderr с причиной и пустой stdout.
Даже если до ошибки найден корректный run, частичный каталог не выводится.

Когда `decision verify`, `capture`, `evidence` или `investigate` отказывает (exit `2`, при
несовпавшем `--expect-digest` — `4`), причина — одна строка stderr после `error: `;
`decision runs` пишет её так же, без `error: `. Перевод строки, табуляция, ESC и другие
непечатаемые символы из имени файла, ключа документа или ответа валидатора записаны в ней
escape-последовательностью Python (`\n`, `\t`, `\x1b`); буквы любого алфавита и обратная косая
черта печатаются как есть (буква, которую поток не кодирует, — escape-последовательностью, как
прежде: `\u043c`). Поэтому `\n` в строке — либо записанный перевод строки, либо обратная косая
черта и `n` из самого пути (`C:\new`). Путь, который не прочитан, и файл отвергнутого продукта,
если в них есть непечатаемый символ, стоят в кавычках Python, и обратная косая черта в них
удвоена; без кавычек их `\n` — символы пути. Кавычки бывают и в самом имени, поэтому строка —
для чтения: исходный текст по ней восстанавливается не всегда. Ошибка разбора аргументов
печатается argparse, как прежде, вместе со строкой `usage:`.

`--signing-key` принимает Ed25519 unencrypted PKCS#8 PEM только по явному пути. Private key
хранится вне checkout, evidence и логов; на Mac задайте ему mode `0600`. Соответствующий
public key экспортируется как SPKI PEM и задаётся reader'у через `--trust-key`. Флаг trust
key можно повторить во время ротации. Без signing key создаётся прежний
`integrity=content_hashed`; signed bundle без matching out-of-band public key можно
проинспектировать только как blocked и нельзя читать через `--artifact`.

Форматы входов: semantic ref — каталог с `model.yaml` и необязательным `metrics/`;
dashboard spec — файл по полному ref: `dashboard://sales/weekly_margin@7` читается из
`<dashboards>/sales/weekly_margin@7.yaml` (или `.yml`, `.json`), `dashboard://weekly_margin@7` —
из `<dashboards>/weekly_margin@7.yaml`. Без версии в имени продукт, запинненный на `@7`,
подхватил бы вёрстку `@8`, без каталогов — вёрстку другого дашборда с тем же последним
сегментом. Spec ref из нескольких сегментов, который лежит в корне каталога по последнему
сегменту (так его читал verifier 111), не читается: `bi.contract` даёт review
`bi.spec_missing`, пока файл не перенесён на путь своего ref. Assets — YAML `VerifiedAsset`;
results — `{asset id: ResultSet}` с кандидатными ответами, снятыми вне гейта.
Candidate ref должен держать каждую запинненную продуктом метрику ровно одним
контрактом на запинненной версии, иначе `contracts.validate` блокирует выпуск:
`metric_ref.candidate_version_mismatch`, `metric_ref.candidate_ambiguous`,
`metric_ref.candidate_absent`.
Каждый metric id semantic ref — и base, и candidate — должен определяться одним
контрактом под `metrics/`, в каком бы подкаталоге ни лежал файл. Два контракта одного id,
даже одинаковые, — blocking `gap.metric_duplicate` в `semantic.gaps`: ref не говорит,
какой из них он имеет в виду. Тогда `semantic.diff`, `impact.products`,
`assets.invalidated`, `results.reconcile`, `results.provenance` и `sql.compile` выходят
`check.not_run`, как при контракте, который не загрузился. Старую версию контракта держите
вне `metrics/` и его подкаталогов.
Контракт запинненной метрики в validated paths должен быть определением, которое
держит candidate ref, или определением, которое держит base ref: тогда изменение
candidate — изменение под review. Иначе `contracts.validate` сообщает
`metric_ref.candidate_content_mismatch` и называет отличия кодами semantic diff:
block, когда отличие меняет смысл или ослабляет обещание, advisory при одних
отличиях labels. Валидируйте контракт, который держит выпуск.
На правила продукта, читающие контракт метрики, — dimensions, status и governing
policies — гейт отвечает и на определении, которое держит candidate ref. Dimension
продукта, которую метрика там не объявляет, и status `retired` блокируют выпуск
(`dimension.candidate_not_allowed`, `metric.candidate_retired`); status `draft` и
governing policy, под которой продукт не верифицируется, требуют review
(`metric.candidate_draft`, `policy.candidate_mismatch`); status `deprecated` —
advisory (`metric.candidate_deprecated`). Ответ, который validation уже даёт на
validated-контракте, второй раз не сообщается.
Когда validated paths держат несколько продуктов, `--product` называет верифицируемый,
и `contracts.validate` грейдит issues этого продукта и issues загрузки файлов. Issue,
который validation rules дали другому продукту тех же путей, выпуск не останавливает:
он записан в payload шага как `other_product_issues`, а detail их считает. Файл,
который не прочитан, не прошёл схему или повторяет id, блокирует прогон каждого
продукта, чей бы контракт он ни держал. Схему не проходит и продукт, который дважды
перечисляет один дашборд в `delivery.dashboards` или один формат в `delivery.brief_formats`
(как повтор в `scope.metrics`): `decision validate` называет повтор, а `decision verify`
от такого продукта отказывается, как от продукта, которого нет в путях (`no decision
product …`, exit 2), и в той же строке называет его файл и что отвергла схема:
`; 1 decision product(s) failed the schema: '<id>' in <файл> (<причина>)`. С `--product`
называются файлы продуктов с этим id, без него — каждый отвергнутый схемой (первые три).
За пути целиком отвечает `decision validate`: его exit code следует issues всех продуктов.
Каждая запись `metric_refs` verified asset, переданного прогону, тоже должна пинить
версию, которую держит candidate ref, ровно одним контрактом этой метрики. Иначе
`assets.invalidated` блокирует выпуск с `asset.metric_ref_version_mismatch` или
`asset.metric_ref_absent`. После сдвига версии
перепиньте asset вместе с candidate.

Для агрегатного результата asset может объявить проверки в `tolerance.aggregate_checks`.
Они типизированы по `kind`: `partition_conservation` (сумма частей равна total), `share_sum`
(доли дают целое), `ratio_reconstruction` (ratio восстанавливается из numerator/denominator)
и `additive_decomposition` (компоненты дают total). Имена колонок указываются явно; эвристик
по названиям нет. `absolute_tolerance`, `expected_sum` и `factor` задаются decimal-строками.
Некорректный reference result даёт `insufficient`, а нарушивший инвариант candidate —
`different`; оба исхода исключают clean pass.

Код возврата — вердикт, а не операционный статус: `0` pass / pass_with_advisory, `3`
needs_review, `4` block, `2` usage-ошибка. `1` не используется намеренно — это код
необработанного исключения, и падение не должно выглядеть как вынесенный вердикт.

Ответ decision-команд печатается в любой поток. Если кодировка потока не знает символа
(вывод перенаправлен в файл или pipe под cp1252, задан `PYTHONIOENCODING=ascii`), строка
печатается с backslash-escape на его месте, ответ `--json` — целиком в ASCII с escape самого
JSON, и код возврата остаётся вердиктом. `decision evidence --artifact` печатает payload как
есть или не печатает вовсе: при таком потоке — exit `2` и адрес файла в bundle.

Evidence, которое файловая система не даёт прочитать, — не evidence, которого нет. Если на
manifest, артефакт или каталог bundle нельзя посмотреть или их нельзя прочитать (чужие права,
каталог без права поиска), `decision evidence` отвечает `error: cannot read <путь>: <класс>`
и выходит с `2`, без вердикта; `block` с `evidence.artifact_missing` остаётся ответом на
артефакт, которого действительно нет. Каталог под evidence-путём, в который нельзя заглянуть,
не пропускается: команда просит назвать run через `--run`. `decision verify`, которому после
записи bundle отказано в его повторном чтении (например, umask отнимает чтение у записанного),
кончается `error: cannot read back the evidence bundle under <каталог>: …` и кодом `2`; bundle
при этом записан целиком, и читатель, которому чтение разрешено, его верифицирует.

Гейт не скрывает, чего он не сделал. Шаг «security/access/freshness/physical checks»
требует движка: с `--dwh clickhouse|greengage` гейт спрашивает его read-only ролью и выносит
все пять проверок — `dwh.access` (роль только читает и видит каждую витрину скоупа — иначе block),
`dwh.freshness` (возраст самой свежей строки против `freshness_max_age` продукта — протухший
источник review), `dwh.physical` (колонка метрики, которой на движке нет, — block;
разъехавшийся тип или раскладка — review), `dwh.security` (досягаемость роли, через которую
собрано evidence) и `dwh.cost` (план против `budgets` метрики); без `--dwh` все пять выходят как
`check.not_configured`. Шаг «compile SQL» компилирует
reference-запрос метрики только там, где семантика точна: typed aggregate и governed ratio
на ClickHouse (деление — `divideDecimal` на объявленной `unit.precision`, форма проверена на
живом движке 24.8) и на PostgreSQL-семействе (`TRUNC(num * 1.000000 / NULLIF(den, 0), p)`,
форма проверена на PostgreSQL 14.24). Ratio на движке, которого шов не знает, external и
reviewed-SQL формулы получают названный гэп вместо запроса, за который гейт не может
ручаться. Недоступный кандидатный результат — `needs_review`, никогда не pass. `--created-at` задаётся
явно, когда прогон должен быть байт-в-байт воспроизводимым. `--created-at` (и `--captured-at` у
`decision capture`), который не RFC 3339 UTC timestamp в одном написании, и `--environment` с
одиночным суррогатом — usage-ошибка в одну строку (exit `2`) до чтения входов и до запроса к
движку. Момент в файлах — `review.reviewed_at` verified asset, `formula.review.reviewed_at`
метрического контракта, `attestation.captured_at` записанного результата — пишется так же:
`YYYY-MM-DDTHH:MM:SS`, по желанию дробь секунды, `Z` (`2026-08-20T10:00:00Z`); без секунд,
со смещением вместо `Z` или с текстом после дроби файл отвергается. В YAML момент стоит в
кавычках (`reviewed_at: '2026-08-20T10:00:00Z'`): без них YAML читает его как дату, и отказ
так и говорит. Bundle, чей manifest запечатан до verifier 115 с моментом другого написания
(`created_at`, `signature.signed_at`), больше не читается (`evidence.manifest_invalid`);
момент внутри артефакта заново не читается — артефакт проверяется по digest и размеру. См.
[UTC timestamps](specs/utc-timestamps.md).
Движок `--dwh`, который не ответил, — соединение отвергнуто, сессия потеряна, вопрос упал, —
тоже usage-ошибка в одну строку `error: cannot ask <engine> at <host>:<port>/<database>: …`
(exit `2`, без каталога evidence); отказ движка в доступе остаётся фактом окружения, который
оценивает гейт. См.
[DECISIONOPS_ARCHITECTURE §8.2 и §12.1](DECISIONOPS_ARCHITECTURE.md).

Если проверка `--dwh` не может построить запрос из-за имени временной колонки или
компонента имени таблицы, она возвращает exit `2` и одну строку
`error: cannot build environment query: unsafe identifier: …`, без evidence.
Проверьте указанное имя в кандидатном metric contract. Запрос подсчёта строк и
свежести не отправляется, но соединение и чтение каталогов могут уже состояться.

Если `decision verify --dwh` или `decision capture` не может загрузить настройки,
команда возвращает exit `2` и одну строку `error: cannot load settings: …`, до соединения
и записи результата. Ошибка валидации называет до трёх полей и кодов ошибок, например
`ch_port (int_parsing)`; отказ чтения — только класс исключения. Значения настроек
не выводятся. Offline `decision verify` без `--dwh` настройки не читает.

`run_id` выводится из статических входов прогона: продукта, контрактов в validated paths,
обоих semantic refs целиком, candidate `model.yaml`, dashboard specs и verified assets
продукта, а также `--environment`, signing key и версии verifier. Изменился любой из них —
это другой прогон с другим `run_id`, и в том же каталоге `--evidence` он пишется рядом с
прежним bundle. Записанные результаты, показания движка и `--created-at` в `run_id` не
входят: повтор с другими наблюдениями или в другое время в тот же каталог отклоняется
(usage-ошибка, exit `2`) — такому прогону нужен свой каталог evidence.

С `--dwh greengage` на ядре до PostgreSQL 9.5 (Greenplum 6 и Greengage 6 работают
на 9.4) нет row-level security: гейт не читает row-policy каталог, clean summary
`dwh.security` с покрытием называет версию ядра, остальные проверки среды грейдятся
как на любом ядре.

`evidence` — вторая половина: bundle, который написал `verify`, читается заново и каждый
content address пересчитывается. Аргумент — каталог evidence или один run-каталог внутри
него; если run'ов несколько, команда называет их и просит `--run`, а не выбирает сама.
Вердикт здесь про целостность, а не про релиз: целый bundle выходит с `0`, даже если внутри
записан `block`; подменённый payload или неожиданный `--expect-digest` дают `4`.
Digest, проверка подписи и возвращённый bundle относятся к одним прочитанным bytes
manifest. Это не атомарный snapshot всего каталога: каждый payload проверяется по
адресу из этого manifest при его чтении.
`--artifact ID` печатает один payload — но только если он всё ещё хешируется в свой адрес,
а `restricted`-артефакт не печатается никогда: в манифесте у него есть digest, размер и
класс, и это вся публичная поверхность (§10.2).
ID claim и BI payload, названные по ref, — slug ref для чтения и на конце полный SHA-256
canonical JSON: `[asset id]` у claim, `[dashboard ref, payload id]` у payload
(`claim-asset-sales-weekly-margin-by-region-<64 hex>`,
`bi-dashboard-weekly-margin-7-dataset-1-<64 hex>`): у `weekly_margin` и `weekly-margin`
slug один, хеш разный. ID для `--artifact` берите из списка артефактов `decision evidence`.

`--expect-digest` действует и вместе с `--artifact`: несовпадение manifest digest даёт
`4`, диагностику `evidence.digest_unexpected` в stderr и пустой stdout. При совпадении
stdout содержит только выбранный payload; подпись и ограничения доступа продолжают
проверяться. Этот режим не проверяет содержимое остальных artifacts bundle.

Начинать чтение стоит с `--artifact report-summary-md` — это `reports/summary.md`,
единственный артефакт bundle, написанный для человека: что блокирует релиз, чьи числа
задеты (метрика, дашборд, владелец), что решает человек и **что прогон не сделал**.
Отчёт производный — он пересказывает уже оценённые check'и и не вводит собственных
находок, поэтому спорить с ним бессмысленно: источник истины — сами check'и рядом.

---

## 4. Web UI (`auto_bi serve`)

Открывается на `http://<host>:<port>/` (по умолчанию `127.0.0.1:8200`). Без сборочной
цепочки — статика отдаётся FastAPI.

**Логи (S07):** `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}` (по умолчанию `INFO`) и
`--log-format {text,json}` (по умолчанию `text`). `text` держит собственный цветной консольный
вывод uvicorn; `json` даёт один объект на строку (для ELK/Loki/CloudWatch) и подчиняет ему же
и логи uvicorn (`--log-format json` отключает собственный dictConfig uvicorn). Пример:
`auto_bi serve --log-format json --log-level WARNING`.

**Готовность (S07):** `GET /api/v1/ready` (открыт даже при включённом auth, как `/health`) —
глубокая проверка для оркестратора (compose healthcheck, Fly checks): store + DWH
(`SELECT 1`) + BI (`healthcheck()` на Superset) гейтят `{"ok": false}`/503; LLM-доступность
репортится в том же ответе, но **не** гейтит `ok` (транзиентный сбой
GraceKelly/Anthropic/Mistral не
должен ронять готовность уже собранных дашбордов). Подробнее — ARCHITECTURE §3.11.

**Рестарт (X-4):** сессии переживают перезапуск сервера — открытая вкладка продолжает
работать с тем же диалогом (правки словами, пересборка, ссылка на дашборд); билд,
оборванный рестартом, показывается как ошибка и пересобирается кнопкой «Собрать».
Подробнее — ARCHITECTURE §3.15, DEPLOYMENT §9.

**Три режима ввода:**
- **Текстом** — опишите дашборд словами; агент задаёт уточнения только при реальных
  расхождениях с данными.
- **Полями** (fields-first) — drag&drop (или клик-фоллбек) полей витрин в черновики
  чартов; агент превращает раскладку в spec и показывает детерминированный анализ.
- **Авто** — выберите витрину; детерминированно (без LLM) собирается курируемый обзорный
  дашборд, который сразу попадает в превью — с вердиктами advisor'а, как и текстовый путь
  (текст механический, самих правил: LLM тут не участвует). Дальше — обычная сборка и
  правки словами; первая же правка словами идёт через LLM и вердикты формулируются им.

**Что на экране:**
- **Чат** — уточняющие вопросы, ошибки правок.
- **Превью spec** — карточки чартов + вердикты Feasibility Advisor (полоса акцента по
  severity) и предупреждения. Кнопка **«Собрать / Пересобрать»**, SSE-лог сборки, ссылка
  на дашборд.
- **Выбор BI** — Superset / DataLens (значение шлётся при старте, дальше фиксируется на сессию).
- **Итерации** — после сборки правка словами патчит spec и пересобирает.
- **Заявки владельцу DM** (dm change requests) — если запрос витриной не предусмотрен,
  advisor оформляет заявку; список и статусы видны в UI.
- **Панель «Наблюдаемость»** (сворачиваемая) — трейс шагов агента на сессию (с таймингом
  и исходом) + сводка расходов LLM (вызовы / латентность / объёмы в символах). Подробнее — §5.

---

## 5. Feasibility Advisor и наблюдаемость

**Advisor** — детерминированный (без LLM) контролёр: сверяет запрос с физикой витрины
(ключи сортировки/партиции, размеры, EXPLAIN-evidence) и выдаёт вердикты `info / warn /
critical`. Прямо говорит, когда дашборд витриной не предусмотрен (вплоть до «это запрос на
новую витрину»), и умеет оформить заявку владельцу DM. Правила движко-зависимы (ClickHouse
vs Greenplum/Greengage).

**Наблюдаемость** (Phase 4): для каждой сессии Auto_BI пишет durable-трейс шагов
(grounding / clarify / propose / patch / advisor / approve + фазы сборки) с таймингом и
исходом, и агрегаты по вызовам LLM. API: `GET /api/v1/sessions/{id}/trace` и
`GET /api/v1/observability/llm`.

> **Честность по данным:** при провайдерах `anthropic` и `mistral` usage
> (input/output tokens) пишется
> в Store/`llm_calls` и участвует в opt-in LLM budget (`AUTO_BI_LLM_BUDGET_*`, цены в
> `AUTO_BI_LLM_BUDGET_PRICES`). GraceKelly usage может быть неполным — тогда UI показывает
> измеримое: число вызовов, латентность и **объём в символах** (size-прокси, не доллары).
> Подробнее — ARCHITECTURE §3.9.

---

## 6. Конфигурация (переменные окружения)

Префикс `AUTO_BI_`, читается из окружения или `.env` в корне. Ключевые:

> **Полный перечень** всех полей `Settings` (тип + default) — генерируемый
> [ENV_REFERENCE.md](ENV_REFERENCE.md). После смены default в `auto_bi/config.py`:
> `uv run python scripts/generate_env_reference.py`. Текущее состояние продукта —
> [CURRENT_STATE.md](CURRENT_STATE.md).

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `AUTO_BI_CH_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DATABASE` | ClickHouse DWH (read-only роль) | `localhost` / `8123` / `auto_bi_ro` / `` / `dm` |
| `AUTO_BI_CH_HOST_FROM_BI` / `_PORT_FROM_BI` | CH адрес, как его видит сервер BI (если отличается от CLI-стороны, напр. через туннель) | `` / `0` |
| `AUTO_BI_GP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DATABASE` / `_SCHEMA` | Greenplum/Greengage DWH (v2) | `localhost` / `5432` / `auto_bi_ro` / `` / `postgres` / `dm` |
| `AUTO_BI_SUPERSET_URL` / `_USER` / `_PASSWORD` | Apache Superset | `http://localhost:8088` / `admin` / `` |
| `AUTO_BI_DATALENS_URL` / `_USER` / `_PASSWORD` / `_WORKBOOK_ID` | self-hosted DataLens (v2, experimental live) | `http://localhost:8090` / `admin` / `` (пустой — fail-loud, без shipped default) / `ra7f79yirtumb` |
| `AUTO_BI_CH_HOST_FROM_DATALENS` | CH-хост, как его достаёт DataLens-коннекшн | `host.docker.internal` |
| `AUTO_BI_LLM_PROVIDER` | LLM-провайдер: `anthropic` (прямой Messages API), `mistral` (прямой Chat Completions API) или `gracekelly` (локальный сервис) | `anthropic` |
| `ANTHROPIC_API_KEY` / `AUTO_BI_ANTHROPIC_MODEL` / `_MAX_TOKENS` | Прямой Anthropic API (провайдер `anthropic`). Ключ — стандартная переменная SDK, без префикса `AUTO_BI_`; `AUTO_BI_ANTHROPIC_API_KEY` тоже работает, если ключ нужно держать рядом с остальным `.env` | `` / `claude-sonnet-5` / `16000` |
| `MISTRAL_API_KEY` / `AUTO_BI_MISTRAL_API_KEY` / `_MODEL` / `_URL` / `_MAX_TOKENS` | Прямой Mistral API (провайдер `mistral`); стандартное и `AUTO_BI_`-имя ключа равноправны | `` / `` / `mistral-large-latest` / `https://api.mistral.ai` / `16000` |
| `AUTO_BI_GRACEKELLY_URL` / `_MODEL` | Локальный LLM-сервис (провайдер `gracekelly`) | `http://127.0.0.1:8011` / `claude-sonnet-5` |
| `AUTO_BI_SEND_SAMPLES` | слать ли top-N значений колонок в grounding/propose (только `public`/`internal`; см. `docs/MIGRATION_SEND_SAMPLES.md`) | `false` |
| `AUTO_BI_STORE_PATH` | SQLite-стор (сессии, spec'ы, сборки, llm_calls, заявки DM, users) | `data/auto_bi.sqlite` |
| `AUTO_BI_PRUNE_ON_REBUILD` | удалять ли BI-артефакты прошлой ревизии сессии сразу после успешного ребилда (по владению, не по имени; сбой прунинга не валит билд). `false` — ревизии копятся до ручной чистки `auto_bi prune` | `true` |
| `AUTO_BI_AUTH_ENABLED` | включить аутентификацию + RBAC (см. §8) | `false` |
| `AUTO_BI_AUTH_USERS_FILE` | YAML с пользователями (если пусто — бутстрап-админ) | `` |
| `AUTO_BI_ADMIN_USER` / `_PASSWORD` | бутстрап-админ, когда auth включён и нет users-файла | `admin` / `` |
| `AUTO_BI_AUTH_TOKEN_TTL_HOURS` | срок жизни токена/сессии | `24` |
| `AUTO_BI_AUTH_COOKIE_SECURE` | `Secure`-флаг login-cookie: пусто = авто (вкл., если сервер не на loopback-хосте); `true`/`false` форсирует | (авто) |
| `AUTO_BI_SESSION_RATE_ENABLED` | включить per-IP/per-day квоту на LLM-эндпоинты сессий (см. §7) | `false` |
| `AUTO_BI_SESSION_RATE_PER_DAY` | лимит LLM-вызовов в сутки на IP, когда квота включена | `100` |
| `AUTO_BI_WORK_RATE_ENABLED` | квота на дорогую non-LLM работу (auto/approve/insights); forced ON при `DEMO_AUTO_ONLY` | `false` |
| `AUTO_BI_WORK_RATE_PER_DAY` | лимит work-операций в сутки на IP | `50` |
| `AUTO_BI_FORWARDED_ALLOW_IPS` | каким прокси доверять `X-Forwarded-For` (реальные IP для квот за reverse-proxy; DEPLOYMENT §3): адреса через запятую или `*`, пусто = только loopback | (пусто) |
| `AUTO_BI_PROFILE` | deployment profile: `local` (dev, без hard-fail), `demo` (auto-only или text+LLM-ready+quota), `production` (auth, strict BI fingerprint, non-default secrets, limits, retention; см. DEPLOYMENT §2) | `local` |
| `AUTO_BI_DEMO_AUTO_ONLY` | режим публичного демо: открыт только авто-обзор, text/fields/правки/enrichment → 403, LLM не подключается вовсе; `/health.capabilities` отражает доступные режимы | `false` |
| `AUTO_BI_REQUIRE_LLM_READY` | при text-профиле отказать в старте `serve`, если LLM-probe не проходит (HF demo forced true когда `DEMO_AUTO_ONLY=false`) | `false` |
| `AUTO_BI_SUPERSET_PUBLIC_URL` | публичная база для ССЫЛОК на дашборд, когда она отличается от `AUTO_BI_SUPERSET_URL`, по которому ходит адаптер (деплой за прокси/демо) | (= superset_url) |

---

## 7. Аутентификация и RBAC (опционально)

По умолчанию API/UI **без аутентификации** (single-user, §2.1). Чтобы включить
многопользовательский режим с разграничением по схемам DWH — `AUTO_BI_AUTH_ENABLED=true`.
Когда включено: API требует bearer-токен (или cookie из логина), а каждый пользователь
ограничен своими **схемами** (часть имени таблицы до первой точки: `dm.sales` → схема `dm`).

**Пользователи** — из YAML-файла (`AUTO_BI_AUTH_USERS_FILE`):

```yaml
users:
  - username: alice
    password: "..."        # плейнтекст здесь = операторский секрет (как .env); храните вне VCS
    role: analyst
    schemas: [dm]          # доступные схемы; ["*"] = все
  - username: root
    password: "..."
    role: admin
    schemas: ["*"]
```

Без файла, но при `AUTO_BI_ADMIN_PASSWORD` — создаётся один admin (`AUTO_BI_ADMIN_USER`, все схемы).
Пароли хэшируются (pbkdf2_sha256) при старте; в Store плейнтекста нет.

**Где действует RBAC:** панель полей и gaps (`/model/*`) показывают только разрешённые
таблицы; агент при старте сессии видит только разрешённые схемы; сборка над таблицей вне
схем пользователя отклоняется (403).

**Эндпоинты:** `POST /api/v1/auth/login {username,password}` → токен + HttpOnly-cookie;
`POST /api/v1/auth/logout`; `GET /api/v1/auth/me`. Программный/CLI-клиент шлёт
`Authorization: Bearer <token>`; web UI использует cookie (вход через форму логина).

**Хардening логина (S06):** `/api/v1/auth/login` лимитирован in-process — 5 попыток/мин
на IP, при превышении 429 + `Retry-After`, лок-аут растёт экспоненциально при повторных
нарушениях (потолок 15 мин). Cookie получает `Secure` автоматически, если сервер не
забинжен на loopback (`127.0.0.1`/`localhost`/`::1`) — форсировать в любую сторону:
`AUTO_BI_AUTH_COOKIE_SECURE`. Токены в SQLite хранятся как `sha256(token)`, не сырым
значением; протухшие строки подчищаются фоновым потоком раз в час.

**Владелец сессии:** при `AUTH_ENABLED=true` каждая сессия привязана к username; чужой
не-admin получает **404** на session-scoped эндпоинтах (существование скрыто). Admin видит
все. При auth off — single-user, owner не проверяется.

**Квота на LLM-вызовы (O-2):** `POST /api/v1/sessions` и `POST /api/v1/sessions/{id}/reply`
(оба дёргают LLM) — opt-in per-IP/per-day (`AUTO_BI_SESSION_RATE_ENABLED`, default off;
`AUTO_BI_SESSION_RATE_PER_DAY=100`). Превышение: 429 + `Retry-After` (плоский суточный
потолок, без экспоненциального lockout).

**Квота на дорогую non-LLM работу (P0-3):** `POST /sessions/auto`, approve/build и insights
жгут DWH/BI/CPU даже без LLM. Opt-in `AUTO_BI_WORK_RATE_ENABLED` / `_PER_DAY` (default 50);
в публичном `demo_auto_only` work-quota **форсируется**, чтобы аноним не зафлудил стенд.
Авто-обзор **не** входит в session/LLM-квоту O-2, но **входит** в work-quota.

---

## 8. Частые ситуации

- **`Semantic model not found: ...`** — нет `model.yaml`. Запустите
  `auto_bi introspect --output semantic/model.yaml` (см. [ONBOARDING_DWH.md](ONBOARDING_DWH.md)).
- **Запрос не выражается в IR** (напр. поле из другой таблицы без join'а) — превью честно
  покажет реальные поля; агент не молчит. Решение — указать join в модели (см.
  ARCHITECTURE §3.4) или переформулировать.
- **Advisor выдал `critical`** — витрина не предусматривает такой паттерн (фуллскан большого
  факта, неколокированный join, фильтр не по партиции). Это не баг: либо примите вариант
  advisor, либо оформите заявку владельцу DM.
- **DataLens-сборка** — нужен живой self-hosted стенд; BI-коннекшн адаптера всегда смотрит в
  ClickHouse (GP-сборка в BI не поддержана — GP используется для интроспекции и advisor).

---

См. также: [ARCHITECTURE.md](ARCHITECTURE.md) (дизайн), [PLAN.md](PLAN.md) (фазы),
[ONBOARDING_DWH.md](ONBOARDING_DWH.md) (подключение нового DWH).
