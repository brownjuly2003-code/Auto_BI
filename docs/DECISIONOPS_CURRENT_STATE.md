# Auto_BI DecisionOps — текущее состояние

**Обновлено:** 2026-09-07

**Стадия:** release gate bootstrap завершён; offline investigation alpha разрешён,
human/design-partner gate остаётся открытым

**Версии evidence:** `VERIFIER_VERSION=8`, `CHECK_VERSION=5`

## Коротко

`Auto_BI_new` — локальный additive fork Auto_BI v0.5.0. **Analytics Release &
Evidence Gate** проверяет governed-семантику, product impact и content-addressed
evidence; унаследованный путь «запрос → DashboardSpec → BI» остаётся baseline.

Рабочий путь DecisionOps:

```text
contracts -> validate -> snapshots/diff -> product impact -> result reconciliation
          -> reference SQL -> DWH checks -> pure BI compile -> evidence bundle/report
```

Offline investigation alpha работает отдельным контуром:

```text
InvestigationPlan + CanonicalMetricResult -> четыре pure v1 skill
       -> evidence-backed observations/gaps -> deterministic brief artifacts
```

- `auto_bi decision validate` проверяет связанные YAML-контракты.
- `auto_bi decision verify` выполняет one-shot gate без удалённой BI-мутации; impact
  доходит до конкретного чарта доставляемого дашборда (`computes_metric` / `reads_field`),
  а нерешаемые плитки выходят именованными `chart_gaps`.
- `auto_bi decision evidence` перепроверяет digest каждого artifact в bundle.
- `auto_bi decision investigate <plan.yaml> --results <result.yaml> ... --output <dir>`
  запускает только offline investigation skills и требует новый output-каталог.
- Без `--dwh` engine checks дают `check.not_configured`; с `--dwh` работают
  `dwh.access`, `dwh.freshness`, `dwh.physical`, `dwh.security` и `dwh.cost`.
- Exit codes: `0` pass/advisory, `3` needs_review, `4` block, `2` runtime/usage error.

Remote и package identity fork не настроены; `autobi-agent==0.5.0` — baseline.

## Bootstrap и pilot

| Область | Состояние |
|---|---|
| Tasks 1–7 | завершены |
| Task 8: технический pilot | завершён на ClickHouse 24.8.14.39 |
| Task 8: proxy-owner review | v7: scope и actionability проходят |
| Task 8: human owner outcome | открыт: реальное время и понятность отчёта не измерены |
| Offline typed investigation alpha | реализован для supplied canonical results; trust `exploratory` |
| Continuous platform | заблокирована до human/design-partner evidence |

Четыре tracked pilot-сценария, найденные дефекты и Mac verifier 7 evidence описаны в
[pilot rehearsal](DECISIONOPS_PILOT_2026-08-25.md) и `docs/pilot/`; оба Mac bundles
сохранили ожидаемые verdict и integrity `pass`.
[Симуляция owner-gate](DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-04.md) проверила
scope/actionability только как proxy. Task 8 human outcome остаётся открытым,
а [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) не разблокирует
scheduler, PostgreSQL, REST/MCP, notifications или actions.

## Карта реализации

| Ответственность | Код |
|---|---|
| Contracts, product/chart impact | `auto_bi/decision/`, `auto_bi/semantic/` |
| Verification, DWH probe и pure BI compile | `auto_bi/verification/` |
| Evidence и owner report | `auto_bi/evidence/`, `auto_bi/verification/summary.py` |
| Typed offline investigation | `auto_bi/investigation/model.py`, `data.py`, `skills.py`, `claims.py`, `application.py` |
| CLI | `auto_bi/cli.py` |

Нормативный дизайн и обязательные инварианты описаны в
[DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md), решение об additive fork —
в [ADR 0003](adr/0003-decisionops-additive-control-plane.md).

## Что ещё не реализовано

- Для external/reviewed-SQL формул и неизвестного семейства движков reference SQL
  остаётся явным semantic gap; такая формула не несёт термов, поэтому и до плитки
  не разрешается (`chart.formula_untyped`).
- Chart-разрешение не заменяет lineage: плитка на операторском `raw_sql` остаётся
  `chart.opaque_sql`, а связь «чарт считает метрику» — вывод по точному совпадению
  термов, потому что в IR у чарта нет ссылки на метрику.
- `dwh.cost` не доказывает `max_scanned_bytes` и `timeout_ms`, если планировщик
  движка не отдаёт такие данные.
- Gate сравнивает переданные результаты, но сам не выполняет **verified-asset SQL**;
  provenance результата остаётся внешним evidence.
- Нет измеренного человеком owner outcome: proxy-review не заменяет время и
  подтверждение понятности от живого владельца.
- Production-certified brief, scheduler, PostgreSQL platform runtime, MCP/actions,
  **Ossie**, **MetricFlow** и **column-level lineage** не реализованы. Offline brief
  остаётся `exploratory` и не является human acceptance evidence.

## Быстрая проверка

```bash
uv run pytest tests/test_decision_verify.py tests/test_decision_cli.py \
  tests/test_decision_impact.py tests/test_decision_chart_impact.py -q
uv run pytest tests/test_investigation_plan.py tests/test_investigation_skills.py \
  tests/test_investigation_brief.py tests/test_investigation_cli.py -q
uv run auto_bi decision --help
uv run auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-margin/evidence \
  --artifact report-summary-md
```

На собственных контрактах:

```bash
uv run auto_bi decision validate contracts/
uv run auto_bi decision verify contracts/ --base semantic/base \
  --candidate semantic/head --dashboards dashboards/ --evidence .evidence
uv run auto_bi decision evidence .evidence
uv run auto_bi decision investigate tests/fixtures/investigation/weekly_margin.yaml \
  --results tests/fixtures/investigation/revenue_results.yaml \
  --output .investigation-example
```

`investigate` пишет ровно четыре файла: canonical `plan.json`, canonical
`results.json` с входами и ordered skill outputs, typed `brief.json` и
deterministic `brief.md`. План фиксирует `max_queries=0`; application layer не получает
DWH/BI executor и не импортирует remote adapters. Missing/duplicate metric, budget или
scope stop завершаются кодом `2` до записи output-каталога. Unsupported или ambiguous
аналитический вход остаётся evidence-backed gap без factual claim.

Флаг `--dwh clickhouse|greenplum` включает пять read-only engine checks. На этой
Windows-машине для документационной проверки не требуется поднимать WSL/Docker.

## Решения для текущего scope

1. Human owner review остаётся внешним gate и не подменяется синтетической оценкой.
2. До публикации fork остаётся без remote и использует текущую локальную package identity.
3. Offline alpha использует `integrity=content_hashed`; подпись и trust root отложены.
4. Реализованный alpha-срез ограничен supplied canonical results и локальными
   plan/results/brief artifacts; continuous runtime и внешние actions не начинаются.
5. Human/design-partner review остаётся hard gate перед production-certified brief,
   scheduler, PostgreSQL, REST/MCP, notifications и actions.

## Источники истины

1. Текущий Git status, код и [целевая архитектура](DECISIONOPS_ARCHITECTURE.md).
2. [ADR 0003](adr/0003-decisionops-additive-control-plane.md),
   [ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md) и tracked pilot evidence.
3. Для baseline v0.5.0 — [ARCHITECTURE.md](ARCHITECTURE.md) и [CURRENT_STATE.md](CURRENT_STATE.md).
