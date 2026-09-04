# Auto_BI DecisionOps — текущее состояние

**Обновлено:** 2026-09-04

**Стадия:** технический bootstrap release gate завершён; измерение результата у владельца открыто

**Версии evidence:** `VERIFIER_VERSION=7`, `CHECK_VERSION=5`

## Коротко

`Auto_BI_new` — локальный additive fork Auto_BI v0.5.0. Новый продукт —
**Analytics Release & Evidence Gate**: он проверяет изменение governed-семантики,
оценивает влияние на продукты и формирует content-addressed evidence. Унаследованный
путь «запрос → DashboardSpec → BI» сохранён как baseline.

Рабочий путь DecisionOps:

```text
contracts -> validate -> snapshots/diff -> product impact -> result reconciliation
          -> reference SQL -> DWH checks -> pure BI compile -> evidence bundle/report
```

- `auto_bi decision validate` проверяет связанные YAML-контракты.
- `auto_bi decision verify` выполняет one-shot gate без удалённой BI-мутации.
- `auto_bi decision evidence` перепроверяет digest каждого artifact в bundle.
- Без `--dwh` engine checks дают `check.not_configured`; с `--dwh` работают
  `dwh.access`, `dwh.freshness`, `dwh.physical`, `dwh.security` и `dwh.cost`.
- Exit codes: `0` pass/advisory, `3` needs_review, `4` block, `2` usage error.

У репозитория нет configured remote. Имя и версия публикуемого DecisionOps-пакета
не выбраны; `autobi-agent==0.5.0` в PyPI относится к baseline, а не к этому fork.

## Bootstrap и pilot

| Область | Состояние |
|---|---|
| Tasks 1–7 | завершены |
| Task 8: технический pilot | завершён на ClickHouse 24.8.14.39 |
| Task 8: proxy-owner review | v7: scope и actionability проходят |
| Task 8: owner outcome | открыт: время чтения и понятность отчёта |
| Следующая платформенная фаза | не начата; требуется решение владельца |

Tracked pilot rehearsal от 2026-08-25 содержит четыре воспроизводимых сценария на
графе из двух продуктов. Входы, bundles и отчёт находятся в
[DECISIONOPS_PILOT_2026-08-25.md](DECISIONOPS_PILOT_2026-08-25.md) и `docs/pilot/`.
Репетиция выявила три дефекта: неверный scope impact, примесь чужих assets и тихую
перезапись bundle с тем же `run_id`; исправления защищены тестами.

Mac pilot verifier 7 проверил timezone change `metric://sales/revenue@3` для
зависимого `weekly_revenue` и независимого `weekly_orders`. Локальные bundles:

- `.pilot-work/2026-09-02-mac-revenue-tz/evidence-v7-revenue-catalog`:
  `run-7d96f81f2ac4009f`, exit `4`, `block`, digest `d7dc80b9…adc68`;
- `.pilot-work/2026-09-02-mac-revenue-tz/evidence-v7-control-catalog`:
  `run-fae00f0db7e5a6ff`, exit `3`, `needs_review`, `impact.none`,
  digest `d158f681…dfb6e6`.

Оба: 19 artifacts, integrity `pass`. На native ClickHouse 24.8.14.39
access/freshness/physical прошли; `dwh.security` вернул `security.not_observed`,
поскольку временной роли недоступны access-каталоги. Verdict сохранились. Повторять
pilot до решения о необходимости security parity с v6 не требуется.

[Симуляция owner-gate](DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-04.md) оценила
decision surface affected/control в 52/33 секунды и подтвердила понятность scope
и следующего действия. Это proxy-оценка, а не человеческий замер; Task 8 остаётся
открытой.

## Карта реализации

| Ответственность | Код |
|---|---|
| Контракты продуктов и метрик | `auto_bi/decision/spec.py`, `auto_bi/semantic/metric_contract.py` |
| Validate, use cases и impact | `auto_bi/decision/validate.py`, `use_cases.py`, `impact.py` |
| Semantic snapshot и diff | `auto_bi/semantic/gateway.py`, `diff.py` |
| Result/reference SQL checks | `auto_bi/verification/reconcile.py`, `sql_contract.py` |
| DWH checks и read-only probe | `auto_bi/verification/environment.py`, `environment_probe.py` |
| Pure BI compile | `auto_bi/verification/bi_contract.py` |
| Оркестрация и verdict | `auto_bi/verification/release_gate.py`, `model.py` |
| Evidence и owner report | `auto_bi/evidence/`, `auto_bi/verification/summary.py` |
| CLI | `auto_bi/cli.py` |

Нормативный дизайн и обязательные инварианты описаны в
[DECISIONOPS_ARCHITECTURE.md](DECISIONOPS_ARCHITECTURE.md), решение об additive fork —
в [ADR 0003](adr/0003-decisionops-additive-control-plane.md).

## Что ещё не реализовано

- Для external/reviewed-SQL формул и неизвестного семейства движков reference SQL
  остаётся явным semantic gap.
- `dwh.cost` не доказывает `max_scanned_bytes` и `timeout_ms`, если планировщик
  движка не отдаёт такие данные.
- Impact доходит до dashboard ref, но не строит **chart-level impact**.
- Gate сравнивает переданные результаты, но сам не выполняет **verified-asset SQL**;
  provenance результата остаётся внешним evidence.
- Нет измеренного человеком owner outcome: proxy-review не заменяет время и
  подтверждение понятности от живого владельца.
- Typed investigations, certified brief, scheduler, PostgreSQL platform runtime,
  MCP/actions, **Ossie**, **MetricFlow** и **column-level lineage** относятся к
  следующей фазе, а не к текущему bootstrap.

## Быстрая проверка

```bash
uv run pytest tests/test_decision_verify.py tests/test_decision_cli.py -q
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
```

Флаг `--dwh clickhouse|greenplum` включает пять read-only engine checks. На этой
Windows-машине для документационной проверки не требуется поднимать WSL/Docker.

## Решения владельца

1. Измерить время чтения двух v7-отчётов и подтвердить, понятны ли scope и действие.
2. Выбрать remote для fork, если нужны push, hosted CI и внешнее review.
3. Решить, нужна ли подпись evidence и какой trust root допустим.
4. Выбрать package identity до любой публикации.

## Источники истины

1. Текущий Git status и код.
2. [Целевая архитектура](DECISIONOPS_ARCHITECTURE.md) и
   [ADR 0003](adr/0003-decisionops-additive-control-plane.md).
3. [Pilot rehearsal](DECISIONOPS_PILOT_2026-08-25.md) и tracked evidence в `docs/pilot/`.
4. [Bootstrap plan](history/DECISIONOPS_BOOTSTRAP.md) и
   [подробный архив реализации](history/DECISIONOPS_IMPLEMENTATION_HISTORY.md).
5. Для baseline v0.5.0 — [ARCHITECTURE.md](ARCHITECTURE.md) и
   [CURRENT_STATE.md](CURRENT_STATE.md).
