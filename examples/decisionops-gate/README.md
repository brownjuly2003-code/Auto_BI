# DecisionOps: гейт в pull request

Пример проверяет изменение `gross_margin_pct` продукта `decision://sales/weekly_margin`.
Исходные файлы скопированы без изменений:

- `semantic/` — `base/` [timezone-пилота](../../docs/pilot/2026-08-25-ch-tz-margin/);
- `contracts/` — все четыре контракта того же пилота;
- `dashboards/weekly_margin@7.yaml` — [chart-impact overlay](../../docs/pilot/2026-09-12-owner-review-chart-impact/):
  типизированные `revenue_by_region`, `margin_by_region` и SQL-чарт `operator_table`.

Рабочий набор контрактов — два product-файла из `contracts/` и метрики из `semantic/metrics/`.
Копии метрик в `contracts/` сохраняют исходный снимок пилота; они не подмешиваются вторым
определением в рабочий набор. Поэтому сравнение текущего примера с самим собой работает
и после демонстрационной смены timezone. `validate` проверяет products с текущими метриками.

Из корня репозитория, в подготовленном через `uv sync` окружении:

<!-- e2:validate -->
```sh
uv run auto_bi decision validate examples/decisionops-gate/contracts/weekly_margin.yaml examples/decisionops-gate/contracts/weekly_revenue.yaml examples/decisionops-gate/semantic/metrics --json
```

<!-- e2:unchanged -->
```sh
uv run auto_bi decision verify examples/decisionops-gate/contracts/weekly_margin.yaml examples/decisionops-gate/contracts/weekly_revenue.yaml examples/decisionops-gate/semantic/metrics --base examples/decisionops-gate/semantic --candidate examples/decisionops-gate/semantic --dashboards examples/decisionops-gate/dashboards --product decision://sales/weekly_margin --environment github-demo --evidence .evidence/e2-example --json
```

Для прогона нужен новый каталог evidence. DWH, assets и results не передаются: пример
проверяет семантику offline, а не наблюдённые числа движка. Старые assets/results пилота
не входят в этот набор входов.

Исходы записаны заранее. `unchanged` сравнивает семантику с самой собой; `yaml-comment`
добавляет только комментарий YAML в `model.yaml` кандидата — digest семантики не меняется
(`semantic.unchanged`). `timezone-change` меняет только `time.timezone` в
`semantic/metrics/gross_margin_pct.yaml` с `Europe/Moscow` на `UTC`, как в пилоте;
обратная смена тоже блокируется. Impact называет
`dashboard://weekly_margin@7#margin_by_region`. Числа — проверки, а не отдельные findings.

| Сценарий | Вердикт | Exit code | Block | Review | Advisory | Всего | Статус шага Action |
|---|---|---:|---:|---:|---:|---:|---|
| `validate` | `pass` | 0 | - | - | - | - | `-` |
| `unchanged` | `pass_with_advisory` | 0 | 0 | 0 | 8 | 15 | `success` |
| `yaml-comment` | `pass_with_advisory` | 0 | 0 | 0 | 8 | 15 | `success` |
| `timezone-change` | `block` | 4 | 1 | 1 | 8 | 15 | `failure` |

Демонстрационный workflow [`decisionops-gate-demo.yml`](../../.github/workflows/decisionops-gate-demo.yml)
зовёт Action по SHA коммита `main`, без `dwh` и секретов. Он читает candidate из checkout головы PR
(`pull_request.head.sha`), base — через `git worktree add` на `pull_request.base.sha`
во временный каталог раннера. Products, метрики и dashboards — из примера в голове PR;
base-семантика — из примера в базовом коммите. Заранее подготовленных каталогов нет.

При `fail-on: block` advisory не роняет шаг, `block` роняет его с exit 4; вердикт, exit code
гейта и `steps.gate.outcome` записываются отдельно. Action по умолчанию обновляет один
комментарий продукта; на PR из fork запись может быть отказана, и отчёт остаётся в summary
([контракт Action](../../docs/specs/pull-request-comment.md)).

Bundle каждого прогона с вердиктом, включая `block`, workflow загружает артефактом
`decisionops-pr-<номер PR>-<run id>-<attempt>` на 14 дней. Последний шаг записывает digest
и имя артефакта в job summary. Оба прогона демонстрационного PR #104 совпали с таблицей выше.
Там же записаны обновление одного комментария, проверка скачанного bundle и независимость uv
от checkout PR: [запись E2](../../docs/pilot/2026-11-17-e2-github-demo/README.md)
([ADR 0013](../../docs/adr/0013-entry-points-to-proven-path.md)).
