# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-25
**Стадия:** офлайновый путь §8.2 работает целиком — validate → freeze snapshots →
gaps → diff → impact → инвалидация assets → reconcile → reference SQL (шаг 6:
aggregate + governed ratio на ClickHouse и на PostgreSQL-семействе) → BI compile →
evidence bundle → отчёт владельцу → чтение bundle.
Bootstrap tasks 2–7 закрыты, открыт только **task 8 (pilot) — решение владельца**.
**Ветка:** `main` · **HEAD:** `a13785e` · 17 коммитов поверх baseline, remote нет
**Suite:** 1766 passed / 20 skipped; `mypy --strict`, ruff, black чисты
**Исходный baseline:** `D:\Auto_BI` @
`b4bfa1d47db013b4529c8355da57c5e637a102d9`

> **Читать сверху вниз не обязательно.** Что открыто и чьё это решение — раздел
> «Открытые решения» в конце; как запустить руками — «Проверить руками за две
> минуты» там же; карта модулей — сразу ниже. Секции по датам — это как оно
> устроено и почему, их читают, когда трогают соответствующий слой.

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
| Компиляция дашбордов без remote-вызовов | `verification/bi_contract.py` | `test_bi_contract` |
| Оркестрация гейта | `verification/release_gate.py` | `test_decision_verify` |
| Отчёт для владельца внутри bundle | `verification/summary.py` | `test_gate_summary` |
| Запись и перепроверка bundle | `evidence/manifest.py`, `evidence/store.py` | `test_evidence_store` |
| Чтение bundle | `evidence/inspect.py` | `test_evidence_inspect` |
| Чтение ФС и разрешение путей | `decision/use_cases.py` | `test_decision_verify`, `test_decision_cli` |
| CLI и exit codes | `cli.py`, `verification/model.py` | `test_decision_cli` |

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
- Честность вместо тихого pass: шаги 6–7 (compile SQL, DWH-проверки) выходят как
  `check.not_implemented` (advisory); недоступный кандидатный результат — review, не
  pass; продукт без verified asset получает `result.no_expectations` (advisory).
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
- `check.not_implemented` остался только у шага 7.
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

## Что ещё не реализовано
- §8.2 шаг 6 частично: external и reviewed-SQL формулы офлайн не компилируются — гейт
  называет гэп вместо запроса; governed ratio на движке вне двух проверенных семейств —
  тоже гэп;
- §8.2 шаг 7: security/access/freshness/physical checks против DWH — гейт объявляет
  его `check.not_implemented`;
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

Exit code — вердикт: `0` pass/advisory, `3` needs_review, `4` block, `2` usage-ошибка.

## Открытые решения — они за владельцем, не за агентом

Офлайновая работа, которая не требует движка, закончилась. Часть этого списка агент
снял сам: чтобы узнать семантику десятичного деления, полный стенд не нужен — хватило
`clickhouse local` того же пина 24.8 и локального `postgresql-14` (14.24) в WSL (см. две
секции про governed ratio). Всё, что осталось, требует движка **с данными** или ответа
владельца, и угадывать эти ответы нельзя:

| # | Вопрос | Что он разблокирует |
|---|---|---|
| 1 | Какой реальный change берём в пилот, на каком стенде, чей owner impact меряем | bootstrap task 8; за ним hard gate §16 — без измеренного pilot outcome scheduler/MCP/actions не начинаются |
| 2 | Есть ли доступ к DWH **с данными** (ClickHouse и/или Greengage) | §8.2 шаг 7 (security/access/freshness/physical) — его нельзя проверить на пустом движке. Семантику деления это больше не блокирует ни на одном из двух семейств: она снята на `clickhouse local` и на PostgreSQL 14.24 |
| 3 | Где будет remote этого репозитория (§18.4) | пуш, CI на стороне GitHub, ревью извне; сейчас 15 коммитов поверх baseline лежат только локально |
| 4 | Нужна ли подпись evidence и какой trust root допустим (§18.5) | `integrity=signed`; сегодня честно `content_hashed`, и подписью это не называется |

Шаг 7 без такого движка начинать **нельзя**: писать security/access/freshness-проверки
«на глаз» — ровно то, от чего гейт отказывается, и агент не должен делать этого за него.
По `CLAUDE.md` это стоппер **S6** (граница фазы): следующую фазу не начинать самому,
перед ней уместен `/cxkm`-ревью по диффу `b4bfa1d..HEAD`.

Что можно делать без этих ответов — только мелкое и не меняющее контракт: удобства CLI
(например, листинг run'ов в `decision evidence`), доки, тесты. Ни один такой пункт не
приближает pilot evidence, поэтому по умолчанию — не начинать и спросить.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
