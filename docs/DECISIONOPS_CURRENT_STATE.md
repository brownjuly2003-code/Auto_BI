# Auto_BI DecisionOps — текущее состояние

**Дата:** 2026-08-24
**Стадия:** contracts + validate + semantic diff + VQR/comparator + evidence writer
(bootstrap tasks 2–6 закрыты)
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

## Что ещё не реализовано
- `auto_bi decision verify` и `auto_bi decision evidence`;
- Analytics Release & Evidence Gate и pure BI compile seam;
- typed investigations, certified brief, scheduler, PostgreSQL, MCP и actions.

## Следующий атомарный slice

One-shot `decision verify` + pure BI compile contracts (bootstrap task 7): гейт по
последовательности §8.2 (validate → freeze snapshots → reject blocking gaps → diff
и impact → инвалидация assets → compile SQL → checks → reconcile → BI-контракты без
remote mutation → evidence bundle), `BIContractCompiler` поверх общих pure builders
Superset/DataLens и mutation-spy тест, доказывающий ноль remote creates. Всё, что
нужно для входа, уже есть: контракты, snapshot/diff/impact, VQR/comparator,
evidence writer, exit codes.

## Источники истины

1. Текущий Git status/HEAD этого репозитория.
2. [DecisionOps architecture](DECISIONOPS_ARCHITECTURE.md).
3. [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
4. [Implementation plan](../decisionops-bootstrap.md).
5. Для унаследованного v1 runtime — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
