# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-25
**Стадия:** офлайновый путь §8.2 работает целиком — validate → freeze snapshots →
gaps → diff → impact → инвалидация assets → reconcile → reference SQL (шаг 6:
aggregate + governed ratio на ClickHouse и на PostgreSQL-семействе) → BI compile →
evidence bundle → отчёт владельцу → чтение bundle. **Шаг 7 закрыт целиком:** под `--dwh`
гейт спрашивает движок read-only ролью — `dwh.access`, `dwh.freshness`, `dwh.physical`,
`dwh.security` и `dwh.cost`, — без него честно говорит `check.not_configured`. Ничего в
гейте больше не отвечает `check.not_implemented`.
Bootstrap tasks 2–7 закрыты, открыт только **task 8 (pilot) — решение владельца**.
**Ветка:** `main` · **HEAD:** `e3dbf30` (последний код — `9a658b4`) · 28 коммитов поверх
baseline, remote нет
**Suite:** 1899 passed / 20 skipped; `mypy --strict`, ruff, black чисты. Известная
предсуществующая флака (не DecisionOps):
`test_runtime_evidence::test_collect_container_snapshot_success` падает при прогоне своего
файла целиком и проходит и в одиночку, и в полном прогоне — порядковая зависимость внутри
файла, воспроизводится и на дереве без этих правок
**Исходный baseline:** `D:\Auto_BI` @
`b4bfa1d47db013b4529c8355da57c5e637a102d9`

> **Читать сверху вниз не обязательно.** Что открыто и чьё это решение — раздел
> «Открытые решения» в конце; как запустить руками — «Проверить руками за две
> минуты» там же; карта модулей — сразу ниже. Секции по датам — это как оно
> устроено и почему, их читают, когда трогают соответствующий слой.

## Если вы начинаете сессию — четыре факта

1. **Кода, который стоит писать без владельца, не осталось.** Шаги 6 и 7 закрыты, дешёвого
   следующего слайса нет; подробности — «Открытые решения» в конце. По `CLAUDE.md` это
   стоппер **S6** (граница фазы): следующую фазу самому не начинать, перед ней уместен
   `/cxkm`-ревью по диффу `b4bfa1d..HEAD`.
2. **Стенды поднимаются одной командой** и нужны для всего, что трогает движок:
   `wsl.exe -d Ubuntu-22.04 -e bash -lc '~/start_stands.sh; exec sleep infinity'` (в фоне).
   Раздел «Стенды, на которых это проверялось».
3. **Живой прогон гейта — ещё две команды**, фикстуру раскладывает
   `scripts/decision_stand_fixture.py`. Там же написано, что должно получиться.
4. **Правило, которым написан весь шаг 7:** проверку против движка не пишут «на глаз».
   Каждое утверждение о том, как ведёт себя ClickHouse или PostgreSQL, в этом документе
   получено на стенде, и почти каждое опровергло то, что казалось очевидным. Если новый
   слайс требует нового утверждения о движке — сначала стенд, потом код.

## Где что лежит

| Что | Модуль | Тесты |
|---|---|---|
| Контракты (product, metric, evidence) | `decision/spec.py`, `semantic/metric_contract.py`, `evidence/model.py` | `test_decision_spec`, `test_metric_contract`, `test_evidence_bundle`, `test_contract_boundary` |
| Канонизация и digest (RFC 8785) | `evidence/canonical.py` | `test_evidence_canonical` |
| Cross-contract проверка | `decision/validate.py` | `test_decision_validate` |
| Снапшот семантики, gaps | `semantic/gateway.py` | `test_semantic_gateway` |
| Semantic diff и impact до владельца | `semantic/diff.py`, `decision/impact.py` | `test_semantic_diff`, `test_decision_impact` |
| Verified assets, сравнение результатов | `verification/repository.py`, `verification/reconcile.py` | `test_verified_assets`, `test_result_reconcile` |
| Reference SQL метрики (§8.2 шаг 6) | `verification/sql_contract.py` | `test_sql_contract` |
| Access, freshness, физический слой, досягаемость роли и стоимость плана (§8.2 шаг 7) | `verification/environment.py` (правила), `verification/environment_probe.py` (IO) | `test_environment_checks`, `test_environment_probe` |
| Компиляция дашбордов без remote-вызовов | `verification/bi_contract.py` | `test_bi_contract` |
| Оркестрация гейта | `verification/release_gate.py` | `test_decision_verify` |
| Отчёт для владельца внутри bundle | `verification/summary.py` | `test_gate_summary` |
| Запись и перепроверка bundle | `evidence/manifest.py`, `evidence/store.py` | `test_evidence_store` |
| Чтение bundle | `evidence/inspect.py` | `test_evidence_inspect` |
| Чтение ФС и разрешение путей | `decision/use_cases.py` | `test_decision_verify`, `test_decision_cli` |
| CLI и exit codes | `cli.py`, `verification/model.py` | `test_decision_cli` |
| Фикстура продукта для живого прогона против стенда | `scripts/decision_stand_fixture.py` | те же формы, что `test_decision_verify` |

## Что сейчас правда

- Создан отдельный локальный Git-репозиторий `D:\Auto_BI_new` с сохранённой
  историей и без configured remotes.
- v1-runtime **функционально** не менялся относительно baseline Auto_BI v0.5.0:
  DecisionOps добавлен рядом (`decision/`, `verification/`, `evidence/`,
  `semantic/metric_contract.py`, `semantic/gateway.py`, `semantic/diff.py`). Ровно
  два файла v1 тронуты, оба — вынос без изменения поведения, чтобы у чарт-пути и у
  гейта была одна функция, а не две копии: `adapters/superset/derive.py` (четыре
  model-производные из `SupersetAdapter`) и `agent/sqlgen.aggregate_expr` (вынесен из
  `_measure_expr`). v1-тесты не правились и зелёные.
- Локальные секреты, окружения, кеши, логи и untracked presentation artifacts
  исходного checkout не переносились.
- Стратегический файл сохранён в корне как
  [`decisionops-strategy-2026-08-21.md`](../decisionops-strategy-2026-08-21.md);
  SHA-256 копии совпадает с `D:\New_steps\bi.md`.
- Целевая архитектура зафиксирована в
  [DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md).
- Решение о fork/additive design зафиксировано в
  [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
- Короткий исполнимый план находится в
  [`decisionops-bootstrap.md`](../decisionops-bootstrap.md).

### Contract kernel (2026-08-24)

- Реализован первый исполняемый слой: `auto_bi/decision/spec.py`
  (`DecisionProductSpec`), `auto_bi/semantic/metric_contract.py` (`MetricContract`),
  `auto_bi/evidence/model.py` (`EvidenceBundle`/`Claim`/`EvidenceArtifact`),
  shared kernel `auto_bi/refs.py` и `auto_bi/scalars.py`.
- `auto_bi/evidence/canonical.py` — RFC 8785 JCS + SHA-256; проходит официальный
  векторный корпус (`tests/fixtures/jcs`, cyberphone/json-canonicalization,
  Apache-2.0), включая UTF-16-порядок ключей и ES6-числа.
- Границы контракта: binary float, unsafe integer, lone surrogate и non-finite в
  contract payload отклоняются (деньги/ratio/tolerance — decimal-строки).
  Negative zero отклоняется уровнем выше, в `scalars.DecimalString`: сам JCS-слой
  обязан печатать оба нуля как `0`, иначе он не JCS.
- Инварианты, закрытые парсингом, а не соглашением: dangling `evidence_ref`,
  `integrity=signed` без проверенной подписи, causal claim, semi-additive без
  `non_additive_axes`, external formula без immutable-пина, unpinned
  metric/policy/dashboard ref, `actions.mode != disabled`, дубликаты в
  scope-списках. Пути артефактов ограничены whitelist-сегментами внутри
  bundle-layout; realpath/symlink-конфайнмент — задача будущего store, не парсера.
- Проверено: 114 тестов ядра + 35 boundary-регрессий; `mypy --strict auto_bi` чист,
  ruff/black чисты. `DashboardSpec`, `BIAdapter` и схема Store не тронуты.
- YAML-фикстуры продукта и метрики: `tests/fixtures/decision/`; sketch §7.2
  архитектуры проверяется docs-as-code тестом.

### `decision validate` (2026-08-24)

- `auto_bi decision validate <paths…> [--json]` — offline cross-contract проверка
  Git-tracked YAML: продукты резолвятся против metric contracts, никаких
  DWH/BI/LLM (тест проверяет, что не открывается ни один сокет).
- `auto_bi/verification/model.py` — `Severity`/`Verdict` и **зафиксированные
  exit codes**: 0 pass/advisory, 2 runtime-usage error, 3 needs_review, 4 block;
  contract-тест написан до CLI (§12.1). Прогон без единого продукта → 2, не 0.
- `auto_bi/decision/validate.py` — стабильные коды находок: `metric_ref.unknown`,
  `metric_ref.version_mismatch`, `dimension.not_allowed`,
  `metric.retired` (block) / `metric.draft` (review) / `metric.deprecated`
  (advisory), `policy.mismatch` (review), `contract.unreadable`,
  `contract.unknown_kind`, `contract.invalid`, `contract.duplicate_id`.
- Verdict считается кодом из severity находок, а `ValidationReport` отклоняет
  вердикт, противоречащий находкам. Порядок находок детерминирован (CI diff).
- Проверено: 29 новых тестов; mypy --strict, ruff, black чисты. Suite — 1503 теста.

> **Известный локальный flake (не DecisionOps, Windows-only).** На полном прогоне
> под нагрузкой периодически падает
> `test_runtime_evidence.py::test_collect_container_snapshot_success`, а также
> таймингвые тесты `test_perf_baseline.py` (наблюдались
> `test_autospec_and_validate_budgets` и `…_backup_budgets`). **Оба класса зелёные
> при одиночном прогоне файла — это первое, что нужно проверить, прежде чем искать
> регрессию.** Механика container-snapshot:
> тест подменяет **глобальный** `subprocess.run`, а `_safe_metadata()` зовёт
> `platform.system()`; на Windows первый в процессе `platform.uname()` уходит
> через `win32_ver()` в `_syscmd_ver()`, который запускает `subprocess.run("ver",
> shell=True)` (CPython 3.13 `platform.py:285,1005`). Результат кешируется в
> `platform._uname_cache`, поэтому падение зависит от того, какой тест прогрел кеш
> раньше — отсюда «то есть, то нет». Разовое лечение — прогреть `platform.system()`
> в фикстуре до подмены `subprocess`. На незагруженной машине прогон зелёный; в CI
> (Ubuntu) этот путь не существует.

### Semantic snapshot + diff + impact (2026-08-24)

- `auto_bi/semantic/gateway.py` — `SemanticSource` (Protocol) и `NativeModelSource`:
  `model.yaml` + metric contracts → immutable `SemanticSnapshot` с canonical graph
  (tables/fields/joins/metrics), `source_digest` (адреса исходных байтов) и
  `canonical_digest` (JCS графа). Снапшот не может соврать про свой digest —
  валидатор пересчитывает. Физическая статистика (`rows`/`bytes`/`cardinality`)
  намеренно вне графа, ключи и партиционирование — внутри.
- Явные `SemanticGap` вместо тихих пропусков (инвариант 6): `gap.raw_sql_metric`,
  `gap.opaque_metric_formula` (external/reviewed SQL), `gap.metric_field_missing`,
  `gap.dangling_join`, `gap.missing_physical`, `gap.contract_invalid`. Гэп несёт
  digest непредставимого значения, не само значение.
- `auto_bi/semantic/diff.py` — классификация по §8.3: removal и weakened
  policy/classification → block; formula/grain/time/unit/additivity/type/agg/role →
  review; label/added → advisory; `physical.keys_changed` → review; появившийся
  гэп наследует свою severity. Порядок изменений детерминирован.
- `auto_bi/decision/impact.py` — конец цепочки: metric → dashboard → product →
  owner, и правило «review → block, если от метрики зависит governed product».
  Chart-уровень честно остановлен на dashboard-ref (нужен `DashboardSpec`).
- Проверено: 45 новых тестов (13 snapshot + 24 diff + 8 impact).

### Adversarial-ревью ядра (2026-08-24)

Независимый ревьюер подтвердил конформность ES6-чисел, UTF-16-порядка ключей и
escaping (независимый оракул, 20k случайных double + свип порядков) и нашёл 9 дыр.
Все закрыты, регрессии — в `tests/test_contract_boundary.py`:

- `$` в рукописных regex совпадает и перед завершающим переводом строки, поэтому
  id, ref, digest, decimal и slug с хвостовым `\n` проходили как валидные — на
  экране неотличимы от настоящих, в хеше другие. Теперь `\Z` (в pydantic-паттернах
  остаётся `$` — там Rust-движок, где `$` уже означает конец строки).
- JCS-слой отвергал `-0.0` и целые вне safe-range вместо сериализации; теперь
  печатает `0` и округляет к double, как требует RFC 8785 §3.2.2.3.
- `digest()`/`canonical_bytes()` перевалидируют bundle: сборка через
  `bundle.claims.append(...)` обходила все валидаторы.
- Контракт, который парсится, обязан хешироваться: целочисленные поля ограничены
  `2**53-1` (иначе отказ всплывал как `CanonicalizationError` на гейте).
- Path confinement — whitelist сегментов: закрыты NUL, `name:stream` (NTFS ADS),
  хвостовые точки/пробелы, device-имена, bidi-override.
- `source_revision` больше не принимает `HEAD`/`main`/`latest` — это не пин.
- Decimal-строки нормализуются (`0.0000010` → `0.000001`), `budgets: {}` ≡ отсутствие
  бюджетов — иначе один контракт давал два digest.
- Blank-строки (`owner: " "`) отклоняются; дубликаты в `scope.metrics`/`dimensions`
  теперь запрещены схемой, как и в `MetricContract`.

### Verified assets + result comparator (2026-08-24)

- `auto_bi/verification/reconcile.py` — типизированное сравнение результатов по §8.4.
  Три исхода, не два: `equivalent` / `different` / **`insufficient`** — вход не смог
  ответить на вопрос. В `insufficient` уходят truncated-результат, float без
  объявленного tolerance, неуникальный или необъявленный business key.
- Порядок правил: схема (имена, типы, nullability, **единицы**) до значений; строки
  матчатся по business key, не по позиции; decimal сравнивается как decimal
  (`0.4120` == `0.412`), float — только внутри абсолютного/относительного tolerance;
  `NULL` / ноль / отсутствующая строка / пустой результат — четыре разных кода;
  порядок строк важен только если контракт так говорит (rank/top-N).
- `auto_bi/verification/repository.py` — `VerifiedAsset` (canonical question +
  paraphrases, intent, metric refs, reference plan, ожидание: result/invariants/
  clarification/refusal, tolerance, reviewer) и `VerifiedAssetRepository`.
  Ожидаемый result без `business_key` отклоняется на парсинге — иначе сравнение
  заведомо `insufficient`.
- Инвалидация (§8.2 шаг 5): asset хранит `semantic_digest`, против которого его
  проверяли; чужой снапшот → `asset.stale_snapshot` (review); изменение метрики с
  severity review/block → `asset.metric_changed`; удаление метрики →
  `asset.metric_removed` (block); advisory-изменение не инвалидирует.
- Проверено: 35 новых тестов (20 comparator + 15 assets).

### Evidence writer + store (2026-08-24)

- `auto_bi/evidence/store.py` — порт `ArtifactStore` и файловый адаптер. Пути строит
  store, а не вызывающий: конфайнмент проверяется повторно уже на уровне ФС
  (resolve внутри `evidence/<run_id>/`, отказ на symlink-компоненте — он резолвится
  «внутрь», но пишет наружу).
- `auto_bi/evidence/manifest.py` — `BundleWriter` (checks → `checks/`, blobs →
  `blobs/sha256/<digest>`, отчёты → `reports/`, restricted → `restricted/`),
  `read_bundle` и `verify_bundle`.
- Свойства, закреплённые тестами: одинаковый вход → байт-в-байт одинаковый
  `manifest.json` и digest; claim без своего артефакта падает **до** записи (на
  диске не остаётся половины bundle); restricted-payload не попадает в публичный
  манифест — там только digest, размер и класс (§10.2).
- `verify_bundle` — вторая половина: `evidence.manifest_missing`,
  `manifest_invalid`, `manifest_not_canonical`, `artifact_missing`,
  `digest_mismatch`. Подделанный blob ловится своим content address.
- Проверено: 17 новых тестов.

### Pure BI compile contracts (2026-08-25)

- `auto_bi/verification/bi_contract.py` — compile-only порт §8.5 и две реализации:
  `SupersetContractCompiler` (virtual datasets → chart `params` → position/native
  filters) и `DataLensContractCompiler` (dataset → wizard `shared` → dash `data`).
  Оба повторяют шаги своего `BIAdapter.build()`, включая D-1 dataset plan и перенос
  top-N LIMIT в form_data для чартов в scope фильтра.
- Второй реализации payload-логики нет: компиляторы зовут те же module-level
  функции, что и адаптеры. Тест сравнивает **объекты** (`bi_contract.build_form_data
  is superset_adapter.build_form_data`), а не результаты — так дублирование нельзя
  внести незаметно. Ради этого из `SupersetAdapter` вынесены в
  `adapters/superset/derive.py` четыре model-производные (`metric_labels`,
  `temporal_alias`, `heatmap_y_pad`, `human_label`/`measure_currency`); методы
  адаптера остались тонкими делегатами, v1-тесты не тронуты.
- Ноль remote mutation доказывается двумя способами: socket-запрет на обоих
  компиляторах и mutation-spy, вооружающий **каждый** глагол `SupersetClient`
  (`request/get/post/put/delete/login`) и `DataLensClient` (`post/gateway/login`).
  Плюс тест, что публичная поверхность компилятора — ровно
  `{capabilities, compile_contract, target}`.
- Что контракт намеренно не несёт (и говорит об этом вслух): remote ids
  (детерминированные `native_id`-плейсхолдеры), DWH connection payload (в нём
  пароль, §10.2) и live-measured RU-масштаб (`_ru_scale` меряет на стенде →
  `live_measured_display_scale=False`, инвариант 6).
- Findings вместо build-time warning'ов: `bi.filter_unwired` (контрол не доезжает ни
  до одного чарта), `bi.filter_partial_scope` (доезжает не до всех — в detail имена
  чартов), `bi.viz_degraded` (DataLens heatmap → pivot), `bi.viz_unsupported`.
- Digest: payload'ы исключены из dump (адреса в контракте, байты — блобами в
  evidence). Digest артефакта = JCS с `allow_float=True` — form_data и конфиги
  DataLens это чужие JSON-документы с IEEE-754 числами, а строгий слой десятичных
  строк управляет нашими контрактами, не их.
- Проверено: 23 новых теста; 215 адаптерных v1-тестов зелёные; `mypy --strict`,
  ruff, black чисты. Suite — 1660 тестов.

### One-shot `decision verify` (2026-08-25)

- `auto_bi/verification/release_gate.py` — последовательность §8.2 для ОДНОГО
  продукта: validate → freeze snapshots → gaps → diff → impact → инвалидация
  assets → reconcile → BI compile → evidence bundle → повторная верификация
  bundle. Оркестрация и только: файлы не читает, сокеты не открывает.
- `auto_bi/decision/use_cases.py` — единственный слой, который читает ФС.
  Semantic ref = каталог с `model.yaml` (+ `metrics/`); dashboard spec = файл
  `<segment>@<version>.yaml`, потому что имя без версии позволило бы продукту,
  запинненному на `@7`, подхватить вёрстку `@8`.
- CLI: `auto_bi decision verify <paths…> --base --candidate --evidence
  [--dashboards --assets --results --product --environment --created-at --json]`.
  Exit code несёт вердикт (0/3/4), usage-ошибка — 2 и текст в stderr.
- `operational_status` и `verdict` — разные поля, и `GateReport` не валидируется,
  если их смешать: check со статусом `error` требует `failed` (инвариант 8), а
  вердикт, противоречащий своим check'ам, отклоняется.
- Честность вместо тихого pass: проверка, которую этому прогону не у кого было спросить,
  выходит как `check.not_configured` (все пять проверок шага 7 без `--dwh`), а не
  отсутствует; недоступный кандидатный результат — review, не pass; продукт без verified
  asset получает `result.no_expectations` (advisory). `check.not_implemented` сегодня не
  выдаёт никто — код остался только в словаре `summary.py`, потому что bundle, записанный
  прежней версией верификатора, его несёт.
- «Reject blocking gaps» останавливает диф ТОЛЬКО на `gap.contract_invalid`
  (`UNTRUSTWORTHY_GAPS`): непрочитанный контракт отсутствует в графе, и diff назвал
  бы это удалённой метрикой. Удалённое поле метрики — правдивый граф с названной
  дырой, поэтому diff и impact продолжают работать и владелец узнаёт, что сломалось.
- Идемпотентность §7.6: `run_id` детерминирован (digest продукта, обоих снапшотов,
  policy, environment, verifier version), `--created-at` задаётся явно → одинаковый
  вход даёт байт-в-байт одинаковый bundle. Тест проверяет это на двух каталогах.
- `CheckResult` без `started_at`/`duration_ms`: часы внутри content-addressed
  артефакта ломают воспроизводимость; тайминг — забота trace (§13), не evidence.
- Проверено: 35 новых тестов (29 сценарных + 6 CLI), включая §15.3 — четыре
  breaking seeds (formula / grain / event time / removed field) блокируются с точным
  impact до владельца и дашборда, presentation-only change не блокирует, прогон не
  открывает сокет и не зовёт ни один метод BI-клиентов. Suite — 1695 тестов;
  `mypy --strict`, ruff, black чисты.

### Чтение evidence — `decision evidence` (2026-08-25)

- `auto_bi/evidence/inspect.py` — вторая сторона `manifest.py`: открывает bundle,
  который здесь никто не писал (скопирован из CI, прислан коллегой, лежит с прошлого
  месяца), и отвечает на два вопроса — тот ли это bundle, за который себя выдаёт, и
  что он утверждает. `BundleInspection` несёт `manifest_digest`, пересчитанный из
  байтов на диске, а не переписанный из манифеста.
- Вердикт про **целостность**, а не про релиз: целый bundle с записанным внутри
  `block` выходит с кодом `0`. Считается тем же `verdict_for`, что и везде, поэтому
  сломанный bundle роняет пайплайн тем же словарём exit code'ов.
- Два отказа сознательные: `restricted`-payload не печатается никогда (§10.2 — в
  манифесте у него только digest, размер и класс), а артефакт, который больше не
  хешируется в свой адрес, не выдаётся вовсе: байты на диске — уже не то, на чём
  принималось решение.
- `--expect-digest` — то, ради чего вообще нужен content address: без него «bundle
  проходит проверку» значит лишь, что каталог внутренне согласован, а не что это тот
  самый bundle, под которым публиковался вердикт.
- Новый код проблемы `evidence.run_id_mismatch` (review, не block): скопированный
  bundle остаётся внутренне согласованным, врёт только его расположение.
- `locate_evidence` в `use_cases.py` (слой ФС) принимает и каталог evidence, и один
  run-каталог внутри него; несколько run'ов — команда называет их и просит `--run`,
  а не выбирает сама.
- Побочно закрыта дыра в `FilesystemArtifactStore`: конфайнмент был укоренён *в*
  каталоге run'а и потому не проверял имя самого каталога — `run_id` вида `../..`
  сдвигал корень. До `--run` все вызывающие были внутренними, известного вектора не
  было; закрыто до того, как значение стало приходить снаружи.
- Проверено: 26 тестов (16 модульных + 10 CLI).

### Отчёт для владельца в bundle (2026-08-25)

- `auto_bi/verification/summary.py` — гейт наконец пишет `reports/summary.md` (§9.2
  называл этот файл с самого начала, но никто его не писал). Единственный артефакт,
  написанный для человека: владелец governed-продукта открывает evidence по чужому
  изменению и не должен читать JSON.
- Порядок разделов владельческий, а не машинный: что блокирует релиз → **чьи числа
  задеты** (метрика, дашборд, владелец и почему review поднялся до block) → что
  решает человек → что прогон НЕ сделал → таблица всех check'ов.
- «Derived» — контракт, а не оговорка: отчёт пересказывает уже оценённые findings и
  не вводит собственных. Каждый check попадает ровно в один раздел — находка,
  повторённая под двумя заголовками, читается как две.
- `result.no_expectations` вынесен в «что не сделано», а не оставлен среди advisory:
  под зелёным вердиктом это самая важная строка — гейт, который не сравнил ни одного
  числа, ничего про числа не проверил.
- `evidence.bundle` в отчёте отсутствует намеренно: эта проверка судит артефакт,
  внутрь которого отчёт запечатан, и выносится уже после запечатывания. Шапка прямо
  говорит, что её вердикт — в run-отчёте, а не здесь.
- Ни часов, ни зависящего от словаря порядка: одинаковый вход даёт те же байты,
  иначе ломается content address (§9.1) и тест байт-в-байт воспроизводимости.
- Читается как `auto_bi decision evidence <path> --artifact report-summary-md`.
- Проверено: 12 тестов. Suite — 1734 passed / 20 skipped; `mypy --strict`,
  ruff, black чисты. Под нагрузкой периодически падает таймингвый
  `test_perf_baseline.py::test_autospec_and_validate_budgets` (в одиночку зелёный) —
  тот же класс, что и описанный выше Windows-flake, не код DecisionOps.

### §8.2 шаг 6 — reference SQL метрики (2026-08-25)

- `auto_bi/verification/sql_contract.py` — гейт компилирует определение каждой метрики
  продукта на её же grain для base и candidate, кладёт обе стороны в bundle и
  показывает, какие запросы поехали. Это не chart-запрос: ни окна, ни фильтра, ни
  top-N — они принадлежат продукту и прогону, не метрике; dimensions — то, чем метрику
  МОЖНО резать, поэтому их нет и в GROUP BY.
- **Граница узкая намеренно.** Компилируется только то, за что можно ручаться офлайн:
  typed aggregate (`SUM(col)` по сгруппированной витрине значит одно и то же в обоих
  движках). Governed ratio на момент этого слайса не компилировался: десятичное деление
  зависит от движка (ClickHouse сохраняет масштаб делимого на `Decimal / Decimal`, и
  chart-путь отвечает на это приведением к Float64 — live-проверенное display-решение,
  которое governed-метрика с объявленной precision наследовать не должна), стенда для
  подтверждения формы не было → `sql.ratio_unverified`. Закрыто ниже, в секции
  «governed ratio на ClickHouse». External/reviewed-SQL — чужое определение, не
  пере-выводится (`sql.opaque_formula`).
- Остальные честные отказы: `sql.join_required` (термин на другой таблице — джойн не
  выдумывается), `sql.metric_absent`, `sql.table_missing`, `sql.engine_undeclared`
  (диалект назван как предположение, а не как факт из модели).
- **Все находки advisory.** Почему метрика поехала, уже оценили diff и impact; вторая
  оценка дала бы одному изменению два веса в вердикте. Шаг несёт evidence, не суждение.
  Отдельно advisory даётся на «запрос поехал»: диалект берётся из объявленного движка
  витрины, поэтому смена движка двигает SQL, не тронув ни одного контракта — этого
  больше не скажет ни один шаг последовательности.
- `agent/sqlgen.aggregate_expr` вынесен из `_measure_expr` и вызывается обоими путями;
  тест сравнивает объекты функции (`sql_contract.aggregate_expr is sqlgen.aggregate_expr`),
  как и для BI-контрактов. Поведение chart-пути не изменилось.
- При `gap.contract_invalid` шаг пропускается вместе с diff/impact/assets/results: метрика
  с непрочитанным контрактом отсутствует в графе, и компиляция назвала бы её метрикой без
  определения (теперь `check.not_run` получают 5 проверок, не 4).
- На момент этого слайса `check.not_implemented` оставался только у шага 7; сейчас его нет
  ни у кого — шаг 7 закрыт целиком (см. секцию про стоимость плана).
- Проверено: 14 новых тестов (13 модульных + 1 на гейте). Suite — 1748 passed /
  20 skipped; `mypy --strict`, ruff, black чисты.

### §8.2 шаг 6 — governed ratio на ClickHouse (2026-08-25)

- **Что разблокировало.** Форму десятичного деления «негде было подтвердить» — стенда
  DWH нет. Но для вопроса о семантике движка полноценный стенд и не нужен: тот же
  бинарь ClickHouse умеет `clickhouse local`. Взят релиз `v24.8.14.39-lts` (это то, во
  что резолвится пин `clickhouse/clickhouse-server:24.8` из `docker-compose.yml`),
  запущен на iMac (`~/chlocal/clickhouse24 local`, macOS-бинарь из GitHub Releases,
  ~81 МБ, self-extracting). Windows-машина в этом не участвует: Docker Desktop не
  поднят, и поднимать его ради одного вопроса дороже, чем один бинарь по ssh.
- **Что стенд сказал такого, чего нельзя было угадать:**
  - `divideDecimal(num, den, p)` делит точно и усекает **к нулю** на `p` (2/3 при p=4 →
    `0.6666`, −2/3 → `-0.6666`), каким бы ни был масштаб термов; результат — `Decimal(76, p)`;
  - **`divideDecimal` читает значения знаменателя ПОД NULL-маской.** `NULLIF(den, 0)`,
    привычный по chart-пути, его не спасает: `divideDecimal(x, nullIf(0, 0), 4)` даёт
    `Code: 153 ILLEGAL_DIVISION`. Ноль обязан быть подменён ВНУТРИ вызова, а строка
    занулена охраной снаружи. Обычное `/` NULL-маску уважает — на нём это не видно;
  - аргументы обязаны быть Decimal: `divideDecimal(2, 3, 4)` → `Code: 43`. Счётные и
    целочисленные термы приводятся к `Decimal(38, 0)` (уширение без потерь);
  - группа из одних NULL суммируется в NULL, а `CAST(NULL AS Decimal(...))` — это
    `Code: 349`; поэтому у nullable-целочисленного терма приведение тоже nullable;
  - альтернатива «привести делимое к нужному масштабу и делить обычным `/`» отпала на
    том же стенде: `Decimal(76, 0) / Decimal(38, 2)` → `Code: 69` (масштаб результата
    меньше масштаба делителя), то есть форма зависела бы от объявленных масштабов обеих
    колонок, а `divideDecimal` — ни от одного.
- **Что теперь компилируется.** `CASE WHEN COALESCE(SUM(den), 0) = 0 THEN NULL ELSE
  divideDecimal(SUM(num), COALESCE(nullIf(SUM(den), 0), 1), <precision>) END`. Нулевой
  или отсутствующий знаменатель = метрика не определена → NULL в строке, а не падение
  запроса. `unit.precision` впервые получил потребителя: он и есть масштаб результата,
  и его изменение двигает digest запроса — ревьюер видит это как изменение метрики.
- **Проверено не только тестами.** Текст, который печатает компилятор, прогнан на том же
  стенде против засеянной `dm.sales_daily`: 300/400 → `0.75`, знаменатель 0 → `NULL`,
  −200/300 → `-0.666666`, счётный вариант → `0.5`. `EXPLAIN` разбирает запрос — шагу 7
  есть что исполнять.
- **Граница на момент этого слайса.** Postgres/Greengage: `numeric`-деление
  масштабирует по своим правилам, стенда для него не было → `sql.ratio_unverified`.
  Закрыто ниже, в секции про PostgreSQL-семейство.
  AVG и Float-колонки → `sql.ratio_inexact_term` (сумма float зависит от порядка
  слагаемых и не адресует governed-число). Колонка, которой нет в модели, →
  `sql.term_undeclared`: без объявленного типа точный терм не отличить от float.
- `null_semantics` в SQL намеренно не кодируется: NULL-пропуск в SUM совпадает с обоими
  прочтениями всюду, кроме группы из одних NULL, а решать это в SQL значило бы менять и
  aggregate-путь.
- Проверено: 11 новых тестов в `test_sql_contract` на месте одного удалённого (suite
  1758 passed / 20 skipped, было 1748), плюс переписанные утверждения в тесте гейта и в
  `test_gate_summary`. Фикстурный продукт больше не даёт `sql.not_compiled`: его ratio
  компилируется, и шаг 6 ушёл из раздела «что не сделано» в отчёте владельцу. Отдельно
  прогнан текст, который печатает компилятор, — на стенде против засеянной витрины.

### §8.2 шаг 6 — governed ratio на PostgreSQL-семействе (2026-08-25)

- **Что разблокировало.** То же, что и на ClickHouse: движок нужен был не «с данными», а
  чтобы ответить на вопрос о семантике деления. `postgresql-14` уже стоял в WSL
  (Ubuntu 22.04, PostgreSQL **14.24**) — `initdb` во временный каталог, порт 55432,
  unix-сокет, без сети. Это реализация `numeric`, которую наследуют Greenplum и
  Greengage; живого Greengage-стенда не было, и в доке это сказано, а не замазано.
- **Что стенд сказал такого, чего нельзя было угадать:**
  - `numeric / numeric` точно, но **масштаб частного выбирается по операндам** под ~16
    значащих цифр. На большом частном это меньше объявленной precision, и
    `TRUNC(a / b, 18)` дописывает нули, которых деление не считало:
    `1000000000000.00 / 3.00` при 18 даёт `333333333333.333333330000000000`;
  - лечится **умножением делимого на точную единицу** с объявленным числом знаков
    (`* 1.000000`): масштаб поднимается, значение не двигается, и цифры становятся
    настоящими — `…333333333333333333`. Приведение `numeric(_, p)` на это место не годится:
    оно **округляет** (`200.50::numeric(1000,0)` → `201`);
  - при `precision` 0 множитель обязан остаться десятичным: `/` над двумя целочисленными
    термами — целочисленное деление (`sum(int) / nullif(sum(int), 0)` → `bigint`), оно
    усекает раньше, чем `TRUNC` увидит число;
  - охраны вокруг деления не нужно: здесь `/` уважает NULL-маску (в отличие от
    `divideDecimal`), поэтому `NULLIF(den, 0)` закрывает нулевой знаменатель, а NULL-терм
    занулевает свою строку сам;
  - `TRUNC` усекает **к нулю** — как `divideDecimal`, так что одно governed-число читается
    одинаково на обоих движках.
- **Что теперь компилируется.** `TRUNC(SUM(num) * 1.000000 / NULLIF(SUM(den), 0),
  <precision>)`. Ни один терм не приводится: множитель уже делает деление `numeric`, а
  целочисленный или NULL-терм не требует того, что нужно ClickHouse.
- **Движок читается по имени, а не по диалекту.** `sqlglot_dialect` подставляет v1-движок
  незнакомому имени — честное допущение для `SUM(col)` и никакое для формы, которая вся из
  engine-specific синтаксиса. Появился `engine.is_known_engine`: `duckdb` теперь получает
  `sql.ratio_unverified` с названным движком, а не молча ClickHouse-форму. Типы термов
  читаются в словаре своего семейства (`numeric`/`decimal`, `integer`/`bigint`/… — целыми
  именами: по префиксу `interval` прочёлся бы как целое).
- **Проверено не только тестами.** Текст, который печатает компилятор, прогнан на стенде
  против засеянной `dm.sales_daily`: 300/400 → `0.750000`, нулевой и отсутствующий
  знаменатель → `NULL`, −200/300 → `-0.666666` (те же числа, что на ClickHouse-стенде),
  большое частное при `precision` 18 → `333333333333.333333333333333333` против
  `…33333333` + девять нулей без множителя, счётные термы при `precision` 0 → `0`.
  `EXPLAIN` разбирает запрос.
- **Граница осталась.** Движок, которого шов не знает, и модель без объявленного движка →
  `sql.ratio_unverified` (у второй ещё и `sql.engine_undeclared`). AVG и float-колонки
  (`double precision`, `Float64`) → `sql.ratio_inexact_term`. Колонка, которой нет в
  модели, → `sql.term_undeclared`. External/reviewed-SQL формула не пере-выводится.
- Проверено: 8 новых тестов в `test_sql_contract` (один из них — на месте удалённого про
  Greenplum-гэп) и 1 в `test_engine`; suite 1766 passed / 20 skipped (было 1758);
  `mypy --strict`, ruff, black чисты.

### §8.2 шаг 7 — access и freshness против живого движка (2026-08-25)

- **Что разблокировало.** Шаг «нельзя начинать без движка с данными» — и он появился:
  ClickHouse **24.8.14.39** (тот же пин, что в `docker-compose.yml`; бинарь из GitHub
  Releases распакован в `~/chstand24` в WSL, порты 8124/9001, свои `users.xml` и
  `config.xml`, ничего общего с чужим 26.7-стендом в `~/chstand`) и PostgreSQL **14.24**
  (`~/pgtest`, порт 55433). Оба засеяны фикстурной `dm.sales_daily` и подняты под
  пользователем, без sudo и без Docker.
- **Модуль разрезан надвое, и это главное решение.** `verification/environment.py` —
  чистый: какие вопросы задать (`questions_for` по метрикам в скоупе продукта), как
  выглядит ответ (`EnvironmentFacts`) и что он значит (`check_access`, `check_freshness`).
  `verification/environment_probe.py` — IO поверх того же read-only `RunQuery`, что и
  интроспекция, SQL-guard и advisor. Гейт остаётся без сокета: он оценивает факты, и те же
  факты ложатся в bundle как данные, а не как строчка лога. `--dwh` даёт CLI, а
  `use_cases` получает уже готовую функцию-пробу — офлайновый прогон остаётся дефолтом.
- **Гейт никогда не проверяет запись записью.** INSERT «чтобы посмотреть» — это запись в
  чужой DWH. Read-only читается из ответа самого движка, и стенды сказали разное:
  - **ClickHouse:** пользователь из `users.xml` имеет `GRANT INSERT, ALTER, DROP … ON *.*`
    даже при `readonly=1`, а отказывает именно профиль (`Code: 164 READONLY`). По грантам
    корректная read-only роль читалась бы как пишущая — поэтому решает setting, а гранты
    смотрятся только во втором случае (`readonly=0` без write-грантов — тоже read-only);
  - **PostgreSQL:** наоборот, `transaction_read_only` у SELECT-only роли равен `off`, и
    отвечает `has_table_privilege` по витринам скоупа. Отдельно проверяется
    суперпользователь: к нему привилегии не применяются, поэтому он всегда «пишущий».
- **Пустая витрина — не протухшая.** `max(Date)` по нулю строк в ClickHouse возвращает
  `1970-01-01`, а не NULL (в Postgres — NULL). Поэтому рядом с чтением едет `rows_seen`, и
  «строк нет» даёт `freshness.unknown` (review — проверка не прошла), а не «протухло на
  пятьдесят лет».
- **Возраст меряется от `created_at` прогона, а не от стенных часов.** Иначе вердикт
  зависел бы от времени рендера отчёта, а `--created-at` существует ровно для
  воспроизводимости. Дата без времени читается как начало этого дня в UTC, и в тексте
  находки это сказано вместе с объявленным таймзоной метрики — граница суток двигается на
  величину до 14 часов.
- **Градация.** Пишущая роль → **block** (инвариант 3: числа, снятые ролью, способной
  изменить витрину, — не то evidence, за которое отвечает bundle). Отсутствующая или
  нечитаемая витрина → **block** (метрику там просто не посчитать). Протухший источник →
  **review** (сам change не сломан; выпускать ли продукт на старых данных — решение
  владельца, и это та же градация, что у stale verified asset). Окно в годах или
  календарных месяцах → **review**: у `P1M` нет фиксированной длины, и гейт не выбирает
  чужую конвенцию за владельца.
- **Проверено на живых движках, не только тестами** (`--created-at 2026-08-25T12:00:00Z`):
  - read-only роль на ClickHouse-стенде → `dwh.access` pass («gate_ro reads every in-scope
    mart on 127.0.0.1:8124/dm, and only reads»), `dwh.freshness` review: витрина датирована
    2026-08-24, «36.0h before …, read as 2026-08-24T00:00:00Z while the metric declares
    timezone Europe/Moscow», старше объявленных `PT6H`;
  - роль `gate_rw` (`readonly=0`, гранты на запись) → `access.role_writable`, **block**;
  - то же самое с окном `P7D` → обе проверки pass (`freshness.fresh`);
  - Greengage-продукт против PostgreSQL-стенда → `access.read_only` + `freshness.stale`,
    то есть PG-путь проходит целиком через psycopg.
- **Physical: держит ли движок то, что заявляет модель** (`dwh.physical`). Сравнивается
  ровно то, что модель утверждает, и только там, где движок ответил:
  - колонка, из которой сделано число (`MetricNode.fields` — термы формулы, ключи grain,
    event time), которой на движке нет → **block**: метрику там не посчитать;
  - тип, разъехавшийся с объявленным → **review**, и это хуже отсутствия: точная форма
    governed ratio шага 6 выведена именно из объявленного типа, так что `Float64` вместо
    `Decimal(18, 2)` значит, что гейт рассуждал не про то число, которое вернёт запрос;
  - раскладка (`table_engine`, sorting key, partition key, distribution key) → **review**:
    число не меняется, но план и advisor спорили про таблицу, которой нет.
  Типы читаются в написании самой модели: `system.columns.type` на ClickHouse и
  `format_type(atttypid, atttypmod)` на PG-семействе — той же функцией, которой их писал
  greenplum-интроспектор. Косметика дрейфом не считается: `Decimal(18, 2)` и
  `decimal(18,2)` — один тип. Раскладка репортится только там, где проверена: ClickHouse
  отдаёт её из `system.tables`, а distribution/partition Greenplum живут в каталогах, до
  которых не добраться без GP-стенда, поэтому PG-путь оставляет их **не отвеченными**, и
  проверка сравнивает ничего вместо выдуманного несовпадения.
- **Проверено вживую и на дрейфе.** Засеянная витрина = модель → `physical.as_declared` на
  обоих стендах. Отдельная база `dm_drift`, где `revenue` действительно `Float64`, а
  ORDER BY действительно `(store_id, date)`, → `physical.type_drift` вместе с
  `physical.layout_drift` в одном вердикте. Хорошая витрина при этом не трогалась.
- **Границы, которые тут остались, закрыты ниже.** На момент этого слайса не было
  security-половины шага 7 и сравнения стоимости плана с `budgets`; обе сделаны — см.
  секции «security: что роль реально достаёт» и «стоимость плана против `budgets` метрики».
  Что осталось верным про этот слой: прогон без `--dwh` даёт `check.not_configured` — это
  ответ, а не пропуск, и в отчёте владельцу он попадает в раздел «что не сделано».
- Проверено: 59 тестов в двух новых файлах (`test_environment_checks` 38 прогонов из 31
  функции + `test_environment_probe` 21) и 7 на уровне гейта; suite 1832 passed /
  20 skipped (было 1766); `mypy --strict`, ruff, black чисты.

### §8.2 шаг 7 — security: что роль реально достаёт (2026-08-25)

- **Одна проверка, а не две.** `dwh.security` спрашивает про досягаемость роли, через
  которую собрано evidence: сузила ли row-политика прочитанные числа и есть ли у роли доступ
  к колонкам, из которых сделано само число. Это две половины одного факта — обе меняют,
  **о чём** этот bundle, — поэтому и проверка одна.
- **Градация.**
  - колонка, из которой сделана метрика, недоступная роли → **block**: метрику там не
    посчитать. И это отдельная находка от «колонки нет» — разговор «выдайте грант» и
    разговор «витрина потеряла колонку» ведут разные люди;
  - включённый row security, при котором роль не проходит ни одну permissive-политику, →
    **block**: витрина отдаёт **пустой ответ и не отдаёт ошибки**, так что назвать это вслух
    больше некому;
  - политика, применяющаяся к сессии, → **review**: числа настоящие, но это числа
    подмножества, а выпускать ли продукт на подмножестве — решение владельца;
  - роль, обходящая row security (superuser / `BYPASSRLS`), при живых политиках → **review**:
    гейт прочитал строки, которых потребители продукта не видят;
  - объявленный `confidential`/`restricted`, который движок для этой роли ничем не
    ограничивает, → **review**: §10.1 требует, чтобы row/column-ограничение исполняла роль
    или детерминированный rewrite, а не документ. `internal` при этом не считается
    объявлением: это значение по умолчанию у непроставленного поля;
  - access-каталог, недоступный роли, → **review**: «не вижу политику» не значит «её нет».
- **Движки отвечают на разные вопросы, и это выяснено на стендах, а не вычитано.**
  - *колонки*: ClickHouse исполняет `GRANT SELECT(col)` и **прячет** неразрешённое —
    роль с пятью грантами из шести видит в `system.columns` пять колонок. Поэтому
    досягаемость читается из `system.grants` (включая partial revoke — так записывается
    `REVOKE SELECT(gross_margin)` после табличного гранта), а не из каталога. PostgreSQL
    наоборот: `pg_attribute` показывает все колонки, отвечает `has_column_privilege`;
  - *читаемость*: `has_table_privilege(…, 'SELECT')` у роли с колоночными грантами —
    **false**, хотя роль витрину читает; честный вопрос — `has_any_column_privilege`;
  - *политики*: ClickHouse применяет политику только к тем, кого она называет (сессия, не
    названная никем, читает всё; restrictive-только политика при этом фильтрует — 600 → 30
    строк на стенде). PostgreSQL наоборот: при `relrowsecurity` строка обязана пройти хотя бы
    одну **permissive**-политику, поэтому витрина с одними restrictive — или с политиками для
    чужих ролей — отдаёт пустоту без ошибки;
  - *кого называет политика*: оба движка вешают политики и на роли (`apply_to_list`,
    `polroles` с `public`), поэтому членство решают enabled roles сессии, а не имя
    пользователя;
  - *кто уходит из-под неё*: PG-суперпользователь и `BYPASSRLS` не фильтруются вообще.
    Владелец таблицы тоже уходит (если политика не `FORCE`), и это намеренно не моделируется:
    владение витриной несёт `INSERT`, а на этом прогон уже остановил `access.role_writable`.
- **Три ложных ответа, которых больше нет.** Все три нашлись, когда правила пошли против
  живых движков, а не против фикстур:
  - PG-роль с колоночными грантами получала `access.table_unreadable` (**block**) по витрине,
    которую прекрасно читает;
  - CH-колонка без гранта получала `physical.column_absent` (**block**, «в витрине нет
    колонки») — теперь physical молчит там, где security говорит «нет гранта»;
  - отказ движка на любом из этих вопросов **ронял прогон исключением**. Теперь отказ — это
    факт (`_ask` возвращает «не ответил»), а исключение остаётся за тем, что не про доступ:
    оборванное соединение не должно читаться как «политик нет».
- **И freshness больше не хвалит пустоту.** Витрину, которую опустошил row security, роль
  читает — но датировать её нечем; freshness молчит (block уже сказан), а сводка пишет
  `freshness.not_run` вместо «все витрины внутри окна». Это ровно та фраза, которой в отчёте
  быть не должно.
- **Проверено вживую на обоих стендах** (`--created-at 2026-08-25T12:00:00Z`):
  - `gate_ro` на `dm` → `security.unfiltered` (pass): «no row policy applies to it and it may
    read every column the in-scope metrics are made of»;
  - политика `gate_pol … USING store_id = 1 TO gate_ro` на `dm.sales_daily` →
    `security.row_filtered` (review), в детали — имя политики и её фильтр; после прогона
    политика снята;
  - `gate_sec` на `dm_sec` (гранты на пять колонок из шести, плюс своя политика) →
    `security.column_unreadable` (**block**), и в том же прогоне `dwh.physical` =
    `physical.as_declared`: спрятанную колонку никто не назвал отсутствующей;
  - PostgreSQL, `dm_sec.sales_daily` (колоночные гранты + политика для `gate_ro`) →
    `dwh.access` pass (витрина читается — это и есть проверка `has_any_column_privilege`) и
    `security.column_unreadable` + `security.row_filtered`;
  - PostgreSQL, `dm_sec.denied` (RLS включён, политик нет) → `security.row_policy_denies_all`
    (**block**) при `freshness.not_run`.
- **Граница осталась одна.** Стоимость плана против `budgets` метрики не сравнивается:
  `advisor/explain.py` умеет `EXPLAIN ESTIMATE`, но `MetricNode` пока не несёт `budgets`, а
  новое поле в графе двигает `canonical_digest` — это отдельный слайс со своим диффом
  снапшотов, а не довесок к этому. `execution.checks` теперь говорит именно про неё.
- Проверено: 53 прогона в `test_environment_checks` (было 38), 36 в `test_environment_probe`
  (было 21), 41 в `test_decision_verify` (было 37).

### §8.2 шаг 7 — стоимость плана против `budgets` метрики (2026-08-25)

- **Чем это оказалось на самом деле.** Не «дописать проверку», а провести объявленный
  потолок через канонический граф: `MetricBudget` жил только в контракте, а гейт оценивает
  план, скомпилированный из **снапшота**. Взять запрос из снапшота, а бюджет — из файла
  рядом значило бы допустить релиз, оценённый по потолку другой версии метрики. Поэтому
  `MetricNode` теперь несёт `budgets` (§7.3 архитектуры и так перечисляет «physical budgets»
  среди переносимых частей контракта), и это двигает `canonical_digest` **каждого** графа.
  Зафиксированных digest'ов в тестах не оказалось — они везде вычисляются, — так что цена
  свелась к одному: сдвинутый бюджет теперь обязан иметь имя в диффе.
- **Дифф называет обе стороны, но взвешивает их по-разному.** `metric.budget_relaxed`
  (**review**) — потолок подняли или сняли: именно это пропускает сегодня план, который
  гейт отказал вчера, и сказать, что так и хотели, может только человек. Снятая граница —
  самое сильное послабление, а не «большее число»: сравнивать не с чем.
  `metric.budget_tightened` (**advisory**) — потолок опустили: спрятать этим ничего нельзя, а
  если он теперь отказывает релизу, `dwh.cost` скажет об этом сам, и вторая оценка дала бы
  одной правке два веса в вердикте. `budgets: {}` и отсутствие бюджета — по-прежнему один
  контракт, поэтому друг против друга не диффятся.
- **Спрашивается планом, никогда прогоном.** `EXPLAIN ESTIMATE` на ClickHouse и `EXPLAIN`
  **без** `ANALYZE` на PG-семействе. Оценивается reference-запрос шага 6 — тот же самый
  объект, что лежит в bundle: компилятор один, поэтому план нельзя оценить по форме,
  которой в evidence нет. Метрика без объявленного потолка не спрашивается вовсе — оценка,
  которую не с чем сравнить, это запрос, выполненный на чужом складе ради числа, которое
  никто не прочитает.
- **Каждый движок умеет ответить молчанием, которое читается как ноль. Оба случая
  воспроизведены на стендах, а не вычитаны:**
  - ClickHouse отдаёт **пустую** оценку, когда план не читает ни одного парта. На стенде это
    случилось дважды: `SELECT count() FROM dm.sales_daily` (тривиальный счёт из метаданных —
    у сгруппированного `count()` оценка уже есть, 600 строк) и витрина на `ENGINE = Memory`,
    чьи строки запрос **действительно прочитал**. Засчитать пустоту нулём значило бы
    пропускать любой бюджет над витриной не на том движке. Байтов `EXPLAIN ESTIMATE` не
    отдаёт вообще — только `parts`, `rows`, `marks`;
  - PostgreSQL считает строки скан-узла **после** фильтра: `WHERE store_id = 1` превратил 600
    в 30, и это не то, что прочитано. Читается скан-узел, а не корень плана: корневой
    `Aggregate` на стенде сказал `Plan Rows: 60` над `Seq Scan`, сказавшим 600 — вершина
    плана это то, что из него **выходит**, а бюджет про то, что входит. И витрина,
    опустошённая row security, планируется вообще **без скан-узла**: `Result` с
    `One-Time Filter: "false"` и `Plan Rows: 0` — как бюджетное чтение это была бы чистая
    оценка над витриной, которая не отдаёт ничего.
- **Градация.**
  - план дороже объявленного потолка → **review**: число остаётся верным, а выпускать ли
    метрику дороже, чем объявил её владелец, — его решение (та же градация, что у
    протухшего источника);
  - объявленный потолок, на который движок не дал оценки, → **review**: проверка не прошла,
    а не прошла успешно;
  - `max_scanned_bytes` и `timeout_ms` → **advisory** `cost.bound_not_estimable`: их не
    отвечает ни один планировщик, и вывести их (гранулы × ширина колонки; стоимость,
    переведённая в миллисекунды) значило бы выдать арифметику гейта за ответ движка. Это
    граница **гейта**, а не двусмысленность владельца — переформулировать контракт нечем, —
    поэтому advisory, как и границы шага 6;
  - потолок у метрики, для которой шаг 6 не скомпилировал запрос, → **advisory**: гэп уже
    назван там, и вторая оценка дала бы одной границе два веса;
  - витрина, на которой уже сработал `check_access` или `check_security`, не оценивается
    вовсе — план поверх неё взвесил бы один факт дважды.
- **Чистых ответов три, и они значат разное:** `cost.no_budget_declared` (потолков не
  объявлено — эта строка попадает в «What was not done» отчёта владельцу, как и
  `result.no_expectations`), `cost.not_run` (объявлены, но ни одного не удалось измерить),
  `cost.within_budget` (измерены и держатся).
- **Проверено вживую на обоих стендах** (`--created-at 2026-08-25T12:00:00Z`,
  та же фикстурная `dm.sales_daily` на 600 строк):
  - ClickHouse, потолок 500 → `cost.over_budget` (review): «reads 600 rows, over the 500 its
    contract declares (EXPLAIN ESTIMATE: 600 row(s) in 2 part(s), 2 mark(s))»;
  - ClickHouse, потолок 1000 → `cost.within_budget` (pass);
  - PostgreSQL, потолок 500 → `cost.over_budget` с «EXPLAIN (FORMAT JSON): 600 row(s)
    estimated scanning sales_daily» — то самое число, которое корень плана назвал бы 60.
- **Мёртвый код убран, а не оставлен на будущее:** `_not_implemented` в гейте больше некому
  вызывать, и конструктор удалён; сам код `check.not_implemented` остался в словаре
  `summary.py`, потому что bundle, записанный прежней версией верификатора, всё ещё его
  несёт. `advisor/explain.estimate_scan` спрашивает движок о том же и намеренно **не**
  переиспользован: он глотает любое исключение (советчик без измерения всё равно советует),
  а гейту нужна разница между «движок отказал» (факт об окружении) и «соединение оборвалось»
  (сломанный прогон).
- Проверено: 63 прогона в `test_environment_checks` (было 53), 46 в `test_environment_probe`
  (было 36), 47 в `test_decision_verify` (было 41), 28 в `test_semantic_diff` (было 24),
  16 в `test_semantic_gateway` (было 13).

## Стенды, на которых это проверялось

Шаг 7 (все пять проверок) и форма деления шага 6 проверены не «на глаз»: под ними два
движка, поднятых локально, без Docker и без sudo. Следующей сессии их не нужно поднимать
заново — достаточно запустить (одна команда ниже).

| Что | Где | Как поднять | Роли |
|---|---|---|---|
| ClickHouse **24.8.14.39** (пин из `docker-compose.yml`) | WSL Ubuntu-22.04, `~/chstand24`, порты **8124/9001** | `~/chstand24/usr/bin/clickhouse server --config-file=~/chstand24/config.xml &` | `default` (admin), `gate_ro` (профиль `readonly=1`), `gate_rw` (пишущая, для проверки block'а); пароли — `~/chstand24/creds.env`, режим 600 |
| PostgreSQL **14.24** (реализация `numeric`, которую наследуют GP/Greengage) | WSL, `~/pgtest/data`, порт **55433** | `/usr/lib/postgresql/14/bin/pg_ctl -D ~/pgtest/data -o "-p 55433 -k ~/pgtest/sock -c listen_addresses=0.0.0.0" -w start` | `gate_ro` (только SELECT), `gate_rw` (с INSERT/UPDATE/DELETE) |

Обе витрины засеяны фикстурной `dm.sales_daily` (600 строк, последние 30 дней). На
ClickHouse рядом лежит база `dm_drift` — та же витрина, у которой `revenue` действительно
`Float64`, а ORDER BY действительно `(store_id, date)`: на ней проверяется, что
`dwh.physical` ловит реальный дрейф, а не только придуманный тестом.

Для security-половины шага 7 рядом стоят **настоящие ограничения**, а не их имитация:

| Стенд | Объект | Что на нём проверяется |
|---|---|---|
| ClickHouse | база `dm_sec` + SQL-пользователь `gate_sec` (`readonly=1`, пароль `secpass`, гранты `SELECT` на пять колонок из шести, `SELECT ON system.*`) и политика `sec_pol … USING store_id = 1` | спрятанная колонка (`security.column_unreadable`) и то, что `dwh.physical` при этом молчит |
| ClickHouse | политика заводится и снимается по ходу прогона на `dm.sales_daily` | `security.row_filtered` под ролью `gate_ro` |
| PostgreSQL | схема `dm_sec`: `sales_daily` (RLS + политика `p_sales` для `gate_ro` + гранты на пять колонок), `denied` (RLS включён, политик нет), `restr_only`, `public_pol`, `group_pol` | deny-all, `TO PUBLIC`, политика на групповую роль, restrictive-only |

Роли `gate_ro`/`gate_rw` на PostgreSQL ходят через `trust` (пароль не нужен); пароли
ClickHouse — по-прежнему в `~/chstand24/creds.env`.

**Стенды живут, пока жив WSL.** Дистрибутив гаснет вскоре после выхода последнего
`wsl.exe`, унося оба движка вместе с собой. Поэтому оба стенда поднимаются **одним фоновым
процессом**, который их и держит — скрипт `~/start_stands.sh` в WSL стартует ClickHouse и
PostgreSQL и печатает занятые порты:

```bash
wsl.exe -d Ubuntu-22.04 -e bash -lc '~/start_stands.sh; exec sleep infinity'   # в фоне
wsl.exe -d Ubuntu-22.04 -e bash -lc 'ss -ltn | grep -E "8124|9001|55433"'      # проверка
```

Проверять портами, а не `Test-NetConnection`: он сразу после старта скажет «открыт», а через
минуту порт будет закрыт — это не файрвол, это погасший дистрибутив. Скрипт идемпотентен:
повторный запуск на живых стендах просто ничего не меняет. Если `~/start_stands.sh` пропал,
он собирается из двух команд колонки «Как поднять» выше плюс `exec sleep infinity`.

**Чужие порты не трогать.** В той же WSL живёт ClickHouse **26.7** другого проекта
(`~/chstand`, порт 8123) и системный PostgreSQL на 5432 плюс ещё один инстанс на 55432 —
поэтому наши 8124 и 55433. Первый `wsl -d Ubuntu-22.04` после простоя может отвалиться по
`HCS_E_CONNECTION_TIMEOUT`; второй заход поднимает машину.

**Живой прогон гейта против стенда — две команды.** Продукт, метрику, оба снапшота и
дашборд-спеку раскладывает `scripts/decision_stand_fixture.py` — теми же формами, что
использует `tests/test_decision_verify.py`, чтобы живой прогон и сьют спорили об одном
продукте. Второй аргумент — движок, который объявляет модель: он решает, для какого диалекта
шаг 6 скомпилирует reference-запрос, а план, скомпилированный для чужого движка, у этого не
спрашивается. Дальше — обычный `decision verify` (пароль читается из `creds.env`, в команду
руками не подставляется):

```bash
uv run python scripts/decision_stand_fixture.py /tmp/stand-run clickhouse max_scanned_rows=500

AUTO_BI_CH_HOST=127.0.0.1 AUTO_BI_CH_PORT=8124 AUTO_BI_CH_DATABASE=dm \
AUTO_BI_CH_USER=gate_ro AUTO_BI_CH_PASSWORD=… \
uv run auto_bi decision verify /tmp/stand-run/contracts \
        --base /tmp/stand-run/base --candidate /tmp/stand-run/candidate \
        --evidence /tmp/stand-run/.evidence --dashboards /tmp/stand-run/dashboards \
        --dwh clickhouse --environment prod-ch --created-at 2026-08-25T12:00:00Z
```

Ожидаемое на нетронутом стенде: `dwh.access` pass, `dwh.physical` pass, `dwh.security` pass,
`dwh.cost` — `cost.over_budget` (600 строк против потолка 500; с `max_scanned_rows=1000` —
`cost.within_budget`), `dwh.freshness` review, если витрину не пересеивали сегодня. То же
самое на PostgreSQL: `greenplum` вторым аргументом, `AUTO_BI_GP_*` вместо `AUTO_BI_CH_*`,
порт 55433, пароль не нужен (`trust`) — но `dwh.physical` там честно скажет `type_drift`,
потому что модель фикстуры написана в ClickHouse-написании типов.

## Что ещё не реализовано
- §8.2 шаг 6 частично: external и reviewed-SQL формулы офлайн не компилируются — гейт
  называет гэп вместо запроса; governed ratio на движке вне двух проверенных семейств —
  тоже гэп;
- §8.2 шаг 7 закрыт: все пять проверок работают под `--dwh`. Внутри `dwh.cost` осталась
  названная граница — `max_scanned_bytes` и `timeout_ms` не отвечает ни один планировщик, и
  они выносятся advisory-гэпом, а не выводятся арифметикой; закрыть её может только движок,
  который на них отвечает, а не ещё один слайс кода;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Проверить руками за две минуты

Гейт целиком офлайновый, поэтому его не нужно никуда подключать:

```bash
uv run pytest tests/test_decision_verify.py tests/test_decision_cli.py -q   # сценарии §15.3
uv run auto_bi decision --help                                              # три команды
```

На своих файлах (contracts + два каталога с `model.yaml`):

```bash
auto_bi decision validate contracts/
auto_bi decision verify contracts/ --base semantic/base --candidate semantic/head \
        --evidence .evidence --dashboards dashboards/ --created-at 2026-08-25T00:00:00Z
auto_bi decision evidence .evidence                                  # что лежит в bundle
auto_bi decision evidence .evidence --artifact report-summary-md     # отчёт для владельца
```

Если есть read-only доступ к движку, шаг 7 спрашивает его сам — все пять проверок,
включая `dwh.security` и `dwh.cost` (настройки — те же `AUTO_BI_CH_*` / `AUTO_BI_GP_*`, что
у остального CLI). `dwh.cost` молчит, пока метрика не объявит `budgets`, и говорит об этом
прямо (`cost.no_budget_declared`):

```bash
auto_bi decision verify contracts/ --base semantic/base --candidate semantic/head \
        --evidence .evidence --dwh clickhouse --environment prod-ch
```

Без своих файлов то же самое делается на локальных стендах за две команды — раздел
[«Стенды, на которых это проверялось»](#стенды-на-которых-это-проверялось) выше:
`scripts/decision_stand_fixture.py` раскладывает продукт, `decision verify --dwh` его
гоняет, и там же написано, что на нетронутом стенде должно получиться.

Exit code — вердикт: `0` pass/advisory, `3` needs_review, `4` block, `2` usage-ошибка.

## Открытые решения — они за владельцем, не за агентом

Офлайновая работа, которая не требует движка, закончилась. Часть этого списка агент
снял сам, подняв движки локально: семантику десятичного деления — на `clickhouse local`
пина 24.8 и на `postgresql-14` (14.24) в WSL, а весь шаг 7 целиком — на полноценных
стендах ClickHouse 24.8.14.39 и PostgreSQL 14.24 с read-only ролями (см. секции про
governed ratio и про шаг 7). Всё, что осталось, требует **вашего** окружения или ответа
владельца, и угадывать эти ответы нельзя:

| # | Вопрос | Что он разблокирует |
|---|---|---|
| 1 | Какой реальный change берём в пилот, на каком стенде, чей owner impact меряем | bootstrap task 8; за ним hard gate §16 — без измеренного pilot outcome scheduler/MCP/actions не начинаются |
| 2 | Есть ли доступ к **вашему** DWH и под какой ролью | прогон гейта против реального окружения. Все пять проверок шага 7 написаны и прогнаны на локальных стендах обоих семейств (`--dwh`) — включая настоящие гранты, row-политики и оценку плана против объявленного потолка, — поэтому вопрос теперь только про доступ и роль |
| 3 | Где будет remote этого репозитория (§18.4) | пуш, CI на стороне GitHub, ревью извне; сейчас 28 коммитов поверх baseline лежат только локально |
| 4 | Нужна ли подпись evidence и какой trust root допустим (§18.5) | `integrity=signed`; сегодня честно `content_hashed`, и подписью это не называется |

Шаг 7 без движка начинать было **нельзя** — писать access/freshness/security-проверки «на
глаз» ровно то, от чего гейт отказывается, — и он не начинался, пока не встали стенды. Тем
же правилом закрыта и последняя четверть: стоимость плана написана против движков, которые
на неё отвечают, и два их способа ответить молчанием (пустая оценка ClickHouse, скан-узел
после фильтра и его отсутствие на PostgreSQL) найдены на стендах, а не придуманы. **Шаг 7
закрыт целиком**, и по `CLAUDE.md` это стоппер **S6** (граница фазы): следующую фазу не
начинать самому, перед ней уместен `/cxkm`-ревью по диффу `b4bfa1d..HEAD`.

**Дешёвого следующего слайса больше нет.** Всё, что осталось офлайн, — CLI-удобства, доки и
тесты — pilot evidence не приближает. Что приближает, требует ответа владельца или его
окружения (таблица выше), и в первую очередь — какой реальный change берём в пилот. Если
очень нужен код без владельца, ближайшее по смыслу — шаг 6: external и reviewed-SQL формулы
и governed ratio на третьем семействе движков; но и он упирается в то же самое — форму
деления не выводят из документации, её проверяют на живом движке, а третьего стенда нет.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
