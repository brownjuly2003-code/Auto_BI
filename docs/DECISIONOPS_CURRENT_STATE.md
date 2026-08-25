# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-25
**Стадия:** contracts + validate + semantic diff + VQR/comparator + evidence
writer/reader + BI compile contracts + `decision verify` (bootstrap tasks 2–7
закрыты; открыт только task 8 — pilot)
**Ветка:** `main`
**Исходный baseline:** `D:\Auto_BI` @
`b4bfa1d47db013b4529c8355da57c5e637a102d9`

## Что сейчас правда

- Создан отдельный локальный Git-репозиторий `D:\Auto_BI_new` с сохранённой
  историей и без configured remotes.
- Runtime-код пока совпадает с tracked baseline Auto_BI v0.5.0.
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
> `test_runtime_evidence.py::test_collect_container_snapshot_success` (иногда
> вместе с таймингвым `test_perf_baseline.py::…_backup_budgets`). Механика:
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

## Что ещё не реализовано
- §8.2 шаги 6–7: compile base/candidate SQL и security/access/freshness/physical
  checks против DWH — гейт объявляет их `check.not_implemented`;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Следующий атомарный slice

Bootstrap task 8 — pilot gate на одном реальном change (**решение владельца**:
какой именно change, на каком стенде, чей owner impact измеряем). Вся техническая
опора для пилота уже есть: гейт выносит вердикт, а `decision evidence` показывает
владельцу, на чём этот вердикт стоит.

Если пилот решено отложить, следующий технический слайс — шаги 6–7
(`sql.compile` + execution checks поверх `agent/sql_guard.py`, `agent/query_plan.py`
и `advisor/*`), которые сейчас честно объявлены нереализованными. Начинать стоит с
`sql.compile`: он офлайновый (компиляция плана метрики в SQL без DWH), а execution
checks требуют стенда и, значит, того же решения владельца, что и пилот.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
