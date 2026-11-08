# Auto_BI DecisionOps — текущее состояние

**Обновлено:** 2026-11-08 · **Версии evidence:** `VERIFIER_VERSION=129`, `CHECK_VERSION=83`
(что меняла каждая — [spec](specs/evidence-format-versions.md))

Здесь только то, что работает, что не реализовано и что дальше. Поведение описывают
[specs](README.md#поведенческие-контракты-decisionops), дизайн —
[архитектура](DECISIONOPS_ARCHITECTURE.md), срезы —
[журнал статуса](history/DECISIONOPS_STATUS_LOG.md).
Прежний подробный текст файла — `git show 5f7b487:docs/DECISIONOPS_CURRENT_STATE.md`.

**Стадия.** Release gate bootstrap завершён; impact доходит до чарта; read-only capture
reviewed-запроса проверен на живых PostgreSQL и ClickHouse; evidence подписывается Ed25519
(opt-in); offline investigation alpha разрешён. Закрыты этапы hardening (2026-11-03),
мутационного smoke и внешнего источника формул dbt (ADR 0008, S1–S5d, 2026-11-07).
Owner outcome (human/design-partner) не измеряется
([ADR 0007](adr/0007-human-outcome-out-of-scope.md)). Fork не публикуется и не имеет remote:
`autobi-agent` локальной версии `0.6.0.dev0`
([ADR 0009](adr/0009-local-package-identity-no-publication.md)).

## Коротко

`Auto_BI_new` — локальный additive fork Auto_BI v0.5.0
([ADR 0003](adr/0003-decisionops-additive-control-plane.md)). **Analytics Release & Evidence
Gate** проверяет, что изменение governed-семантики не ломает числа продуктов, и пишет
content-addressed evidence; путь «запрос → DashboardSpec → BI» остаётся baseline
([ARCHITECTURE.md](ARCHITECTURE.md), [CURRENT_STATE.md](CURRENT_STATE.md)).

```text
contracts -> validate -> snapshots/diff -> product impact -> result reconciliation
          -> reference SQL -> DWH checks -> pure BI compile -> evidence bundle/report
reviewed asset SQL -> read-only capture -> canonical ResultSet + attestation -> gate input
```

- `auto_bi decision validate` — cross-contract проверка YAML/JSON-контрактов.
- `auto_bi decision verify` — one-shot gate без BI-мутации. Impact доходит до чарта
  (`computes_metric` / `reads_field`); нерешаемая плитка — именованный гэп, например
  `chart.opaque_sql`, `chart.metric_unread`, `chart.formula_untyped`
  ([spec](specs/chart-impact-resolution.md)). `results.provenance` оценивает происхождение
  кандидатного числа (без attestation — `provenance.unattested`), `assets.invalidated` сверяет
  reviewed-запрос со snapshot'ом ([spec](specs/asset-query-governance.md)).
  `--{side}-semantic-manifest` берёт формулы из dbt, `--signing-key` подписывает manifest.
- `auto_bi decision capture` — исполняет reviewed SQL positive asset'а read-only и пишет
  canonical result с attestation, привязанной к digest кандидатного snapshot'а.
- `auto_bi decision evidence` — пересчитывает digest каждого artifact и проверяет подпись
  против pinned `--trust-key` ([ADR 0005](adr/0005-ed25519-evidence-trust-root.md)).
- `auto_bi decision pin` — печатает pin каждой метрики dbt `semantic_manifest.json`;
  `auto_bi decision runs` — список локальных прогонов без проверки целостности.
- Без `--dwh` engine checks дают `check.not_configured`; с `--dwh` (`clickhouse`, `greenplum`,
  `greengage`, `postgres`) —
  `dwh.access`, `dwh.freshness`, `dwh.physical`, `dwh.security`, `dwh.cost`.
- Exit codes: `0` pass/advisory, `3` needs_review, `4` block, `2` runtime/usage error.

`auto_bi decision investigate <plan.yaml> --results <result.yaml> --output <dir>` — offline
investigation: четыре pure version-pinned skill над supplied canonical results пишут в новый
каталог ровно `plan.json`, `results.json`, `brief.json` и `brief.md`. План фиксирует
`max_queries=0`, brief остаётся `exploratory` ([spec](specs/investigation-exact-arithmetic.md)).

## Карта реализации

| Ответственность | Код |
|---|---|
| Contracts, product/chart impact, dbt-формулы | `auto_bi/decision/`, `auto_bi/semantic/` |
| Verification, DWH probe, result capture, pure BI compile | `auto_bi/verification/` |
| Evidence и owner report | `auto_bi/evidence/`, `auto_bi/verification/summary.py` |
| Offline investigation | `auto_bi/investigation/` |
| CLI | `auto_bi/cli.py` |

## Что ещё не реализовано

- External/reviewed-SQL формула и неизвестный движок: термов нет, reference SQL — semantic gap,
  до плитки формула не разрешается (`chart.formula_untyped`).
- Chart-разрешение — не lineage: `computes_metric` для operator SQL не заявляется,
  неразобранный SQL — `chart.opaque_sql`; детектор скрытого чтения знает именованный набор
  конструкций, конструкция вне него — дыра детектора.
- Governance reviewed-запроса отвечает «объявлено ли поле», а не «откуда значение»; что нельзя
  приписать без догадки, — `asset.query_fields_unchecked`. Связывание с `WITH` и CTE
  консервативно там, где оно неоднозначно (ARCHITECTURE §7.5).
- PostgreSQL `max_scanned_bytes` — `cost.not_measured` (review): API 14–18 не даёт
  exact-query scanned bytes (ARCHITECTURE §8.2).
- Gate сам не исполняет **verified-asset SQL** — это делает `decision capture`. Подпись
  аутентифицирует producer и bundle, но не доказывает, что capture runner исполнил запрос.
- Production-certified brief, scheduler, PostgreSQL platform runtime, REST/MCP/actions,
  **Ossie**, **MetricFlow** как движок запросов и **column-level lineage** не реализованы. Из
  dbt читаются только формулы `semantic_manifest.json`; каждая причина остаться `external_ref` —
  строка [инвентаря](specs/dbt-formula-inventory.md); публичные dbt-проекты не прогонялись.
  Ограничения этапа: число проблем reader не ограничено; `--*-dbt-database` не записана в
  provenance; сомнительное написание `relation_name` — review.
- Human owner outcome не измеряется (ADR 0007); proxy- и Astra-review его не заменяют.

## Быстрая проверка

```bash
uv run pytest tests/test_decision_verify.py tests/test_decision_cli.py \
  tests/test_decision_impact.py tests/test_decision_chart_impact.py \
  tests/test_result_provenance.py tests/test_result_capture.py -q
P=docs/pilot/2026-08-25-ch-tz-margin
uv run auto_bi decision verify $P/contracts --base $P/base --candidate $P/candidate \
  --dashboards $P/dashboards --assets $P/assets --results $P/results.yaml \
  --product decision://sales/weekly_margin --evidence .evidence
```

Ожидаемый исход — `block` (смена timezone доходит до продукта) и `provenance.unattested`:
у pilot-asset'а нет attestation. Другие tracked примеры —
[chart-impact](pilot/2026-09-12-owner-review-chart-impact/README.md),
[signed live R3](pilot/2026-09-12-owner-review-live-r3/README.md) и
[dbt formula pin](pilot/2026-11-06-dbt-formula-pin/README.md). Signing key — внешний PKCS#8
PEM вне checkout и evidence.

## Что делать дальше

S6-hygiene по аудиту 2026-11-07: LF во всём дереве, хроника версий в spec, этот файл не
больше 10 КБ, идентичность пакета (ADR 0009). Дальше по плану
владельца — полный `ci_local`, S7 (live-проверка DecisionOps на Mac-стенде в `ci_local`), S8
(выжившие мутанты `sql_guard`, обёртка приватного API sqlglot). Другие срезы выбирает владелец.
Публичные dbt-проекты (`dbt deps` — чужой код) — только по его решению.

Не начинать без нового решения:

- PostgreSQL scanned bytes — только с trusted provider/extension и его контрактом.
- Подпись самим capture runner — нужна trusted boundary, где private key недоступен вызывающему.
- Scheduler, PostgreSQL runtime, REST/MCP, notifications, actions.

## Решения для текущего scope

1. Development gate — модельное ревью Astra xhigh
   ([ADR 0006](adr/0006-modelled-review-development-gate.md)); пока у владельца нет лимита
   Codex — независимый Opus-субагент и мутанты (его выбор 2026-11-07). Human evidence и
   production acceptance им не заявляются.
2. Evidence: `integrity=content_hashed` или opt-in `integrity=signed` (Ed25519, ADR 0005).
3. Investigation alpha ограничен supplied results и локальными artifacts.
