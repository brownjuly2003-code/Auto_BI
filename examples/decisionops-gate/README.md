# DecisionOps: гейт в pull request

Пример проверяет изменение `gross_margin_pct` продукта `decision://sales/weekly_margin`.
Семантика и контракты скопированы без изменений:

- `semantic/` — `base/` [timezone-пилота](../../docs/pilot/2026-08-25-ch-tz-margin/);
- `contracts/` — все четыре контракта того же пилота.

`dashboards/` — снимок инвентаря Superset 4.1.2 со стенда проекта (см. «Снимок дашбордов»):
`weekly_margin@7.yaml` и `inventory.json`. `chart_1` (выручка по регионам) типизирован;
`chart_2` (маржа, SQL-метрика `SUM(gross_margin) / SUM(revenue)`) и `chart_3` (виртуальный
датасет) прочитаны как SQL.

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
обратная смена тоже блокируется. Impact называет дашборд `dashboard://weekly_margin@7`, но
не плитку: маржу считает `chart_2` SQL-метрикой-отношением, а инвентарь типизирует только
`AGG(column)`. `chart_2` и `chart_3` гейт называет прочитанными не целиком
(`chart.sql_partial`, `chart.metric_unread`); вердикт от этого не зависит. Числа — проверки,
а не отдельные findings.

| Сценарий | Вердикт | Exit code | Block | Review | Advisory | Всего | Статус шага Action |
|---|---|---:|---:|---:|---:|---:|---|
| `validate` | `pass` | 0 | - | - | - | - | `-` |
| `unchanged` | `pass_with_advisory` | 0 | 0 | 0 | 7 | 15 | `success` |
| `yaml-comment` | `pass_with_advisory` | 0 | 0 | 0 | 7 | 15 | `success` |
| `timezone-change` | `block` | 4 | 1 | 1 | 7 | 15 | `failure` |

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
и имя артефакта в job summary. Оба прогона демонстрационного PR #104 совпали с таблицей,
записанной тогда для ручной спеки дашборда (advisory 8 из 15: `bi.contract` на ней был
advisory). Там же записаны обновление одного комментария, проверка скачанного bundle и независимость uv
от checkout PR: [запись E2](../../docs/pilot/2026-11-17-e2-github-demo/README.md)
([ADR 0013](../../docs/adr/0013-entry-points-to-proven-path.md)).

## Снимок дашбордов

Прогон PR не обращается к Superset и не получает его учётных данных: он читает `dashboards/`
как есть. Снимок снимается вне PR — оператором или
[доверенным workflow](workflows/inventory-snapshot.yml) ветки по умолчанию — против модели этой
ветки, по сопоставлению ref → дашборд из [`superset-map.json`](superset-map.json):

```sh
AUTO_BI_NO_ENV_FILE=1 uv run --no-env-file auto_bi decision inventory \
    examples/decisionops-gate/semantic --map examples/decisionops-gate/superset-map.json \
    --database 1 --output examples/decisionops-gate/dashboards --replace
```

Exit 0 — снимок полный; 3 — отказанные дашборды названы в `inventory.json`, их прежние спеки
удалены, и гейт ответит на них `bi.spec_missing`; 2 — снимка нет, прежний остался байт в байт.
Исключение — второй сбой файловой системы во время отката: exit 2 называет пути, по которым
лежат прежний и новый снимки, и их надо разобрать руками до следующего снятия.
Учётная запись стенда читает только этот дашборд: `can_read` на `Dashboard` и
`SecurityRestApi` и `datasource_access` на два датасета дашборда. Чужой дашборд ей отвечает
404, его чарт, датасет и база — 403, любая запись — 403. Ограничение держится на доступе к
датасетам: другой опубликованный дашборд над теми же датасетами был бы ей виден.

Снимок может отставать от BI. Свежесть — дело того, кто снимает, а не вердикт: гейт видит только
переданные спеки. Обновлённый снимок оператор предлагает отдельным PR. Шаблон workflow на GitHub
не запускался; до публикации коммита инвентаря он отказывает на закреплении SHA
([спека инвентаря](../../docs/specs/superset-inventory.md)).
