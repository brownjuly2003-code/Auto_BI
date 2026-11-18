# E3: инвентарь как вход прогона PR (2026-11-18)

Запись пункта E3 [ADR 0013](../../adr/0013-entry-points-to-proven-path.md): снимок инвентаря
Superset в репозитории как вход `dashboards` прогона pull request. Контракт —
[спека инвентаря](../../specs/superset-inventory.md), разделы E3. Ветка `e3-inventory-snapshot`
от `main` `26b6495`, опубликована как `0.8.0` (PR #106). Демонстрационный PR #108 прошёл на
снимке на GitHub — раздел «На GitHub»; этим основной путь ADR 0013 закрыт.

## Что добавлено

- `decision inventory --map FILE`: сопоставление ref → id или slug дашборда Superset хранится в
  репозитории (UTF-8 JSON, ids — строки). Правила пар те же, что у `--dashboard`; неизвестные и
  повторные ключи — exit 2 до обращения к Superset и к output.
- `--replace`: прежний снимок проверяется как записанный инвентарём, новый пишется рядом целиком
  и подменяет прежний переименованием. Exit 2 оставляет прежний снимок байт в байт (кроме
  второго сбоя файловой системы во время отката: тогда exit 2 называет пути восстановления); exit 3
  публикует новый целиком — спека отказанного дашборда удаляется.
- `inventory.json` пишет `reader_version: 1`; отчёт D6 без поля принимается как версия 1.
  `VERIFIER_VERSION` и `CHECK_VERSION` не менялись: вердикты E3 не трогает.
- [Шаблон доверенного workflow](../../../examples/decisionops-gate/workflows/inventory-snapshot.yml):
  только `workflow_dispatch` ветки по умолчанию, код инвентаря по полному SHA, секреты Superset —
  только шагу инвентаря, `contents: read`, результат — артефакт. На GitHub не запускался;
  закрепляет опубликованный коммит E3 `af93ee4` (PR #107).
- [Пример](../../../examples/decisionops-gate/README.md): `dashboards/` — снимок со стенда
  вместо ручной спеки E2, `superset-map.json`, раздел «Снимок дашбордов».

## Стенд

Mac, colima-VM, compose-проект `e3`: Superset 4.1.2 (pin проекта) и ClickHouse 24.8.
`dm.sales_daily` — по `model.yaml` примера, 1092 строки; `dm.stores` — 12 строк, для чужого
дашборда. Superset: база 1 «Auto_BI ClickHouse», датасеты `sales_daily` (1),
`weekly_margin_operator` (2, виртуальный: `SELECT region, sum(revenue) AS revenue … GROUP BY
region`) и `stores` (3).

Дашборд `weekly-margin` «Недельная маржа» (id 1, опубликован):

| Чарт | Superset | Как построен | Снимок |
|---|---|---|---|
| `chart_1` «Выручка по регионам» | 1, `echarts_timeseries_bar` | `x_axis` region, `SUM(revenue)` | типизирован |
| `chart_2` «Маржа по регионам» | 2, `echarts_timeseries_bar` | SQL-метрика `SUM(gross_margin) / SUM(revenue)` | SQL: «form_data carries a SQL expression» |
| `chart_3` «Операторский срез» | 3, `table` | сырая таблица виртуального датасета | SQL: «the dataset is virtual» |

Дашборд `stores` (id 2) — чужой для учётной записи инвентаря. Чарты созданы через API; UI при
сохранении пишет `query_context`, API — нет. Без него Superset 4.1.2 не описывает в ответе
датасетов колонку `x_axis`, и бар читался как SQL. На стенде `query_context` сохранён так, как
его сохраняет UI ([бэклог](../../DECISIONOPS_BACKLOG.md), E4a).

## Учётная запись только для чтения

Роль `inventory_reader`: `can_read` на `Dashboard` и `SecurityRestApi`, `datasource_access` на
датасеты 1 и 2. Без `SecurityRestApi` не выдаётся CSRF-токен, и запись отвечает 400 (CSRF), а не
отказом прав. Проба этой учётной записью, с CSRF-токеном:

| Запрос | Ответ |
|---|---|
| `POST /security/login`, `GET /security/csrf_token/` | 200, 200 |
| `GET /dashboard/weekly-margin`, `/charts`, `/datasets` | 200, 200, 200 |
| `GET /dashboard/stores`, `/charts`, `/datasets`; `GET /dashboard/2` | 404 |
| `GET /chart/4`, `/dataset/3`, `/database/1` | 403 |
| `PUT /dashboard/1`, `PUT /chart/1`, `POST /chart/`, `DELETE /chart/3`, `PUT /dataset/1` | 403 |

Ограничение «только названные дашборды» держится на доступе к датасетам: другой опубликованный
дашборд над датасетами 1 и 2 был бы ей виден.

## Живые прогоны инвентаря

Код ветки E3, учётная запись `inventory_reader`, `AUTO_BI_NO_ENV_FILE=1`, модель — `semantic/`
примера, `--map superset-map.json`. «Байты» — sha256 дерева каталога.

| | Прогон | Exit | Итог |
|---|---|---:|---|
| A | новый каталог, `--database 1` | 0 | `weekly_margin@7.yaml` (1 типизирован, 2 SQL) и `inventory.json` |
| B | `--replace` поверх ручного каталога E2 | 2 | «missing entry 'inventory.json'»; каталог не тронут |
| C | `--replace` поверх A, те же ответы | 0 | байты те же; рядом ни staging, ни `.previous` |
| D | `--replace`, в map добавлен `stores` | 2 | `GET /api/v1/dashboard/stores -> HTTP 404`; прежний снимок байт в байт |
| E | `--replace` копии A, `--database 2` | 3 | дашборд отказан: три чарта на базе 1; в каталоге остался только `inventory.json` |
| F | `--replace` после E, `--database 1` | 0 | байты как у A |

В B сообщение прогона ещё было «cannot write the inventory … No such file or directory»; после
прогона отказ замены назван отказом («cannot replace inventory …: missing entry
'inventory.json'; --replace takes a directory an inventory wrote»). Снимок A — каталог
`dashboards/` примера.

## Вердикты на снимке

Те же сценарии примера, что в E2, offline:

| Сценарий | Вердикт | Exit | Block / review / advisory / всего | На ручной спеке E2 |
|---|---|---:|---|---|
| `unchanged` | `pass_with_advisory` | 0 | 0 / 0 / 7 / 15 | 0 / 0 / 8 / 15 |
| `yaml-comment` | `pass_with_advisory` | 0 | 0 / 0 / 7 / 15 | 0 / 0 / 8 / 15 |
| `timezone-change` | `block` | 4 | 1 / 1 / 7 / 15 | 1 / 1 / 8 / 15 |

Разница в одном advisory — `bi.contract`: на ручной спеке он был advisory, на снимке — `pass`.
`timezone-change` называет дашборд `dashboard://weekly_margin@7`, но не плитку: инвентарь не
типизирует SQL-метрику-отношение `chart_2`. `chart_2` и `chart_3` названы гэпами
`chart.sql_partial` и `chart.metric_unread`. Типизированный `chart_1` не назван — он не считает
маржу. На ручной спеке E2 плитка маржи была типизирована с `denominator` и называлась
`computes_metric`. Это и есть граница точности инвентаря из ADR 0013 («страдает ответ „какая
плитка затронута“»), она — E4. Вердикт от неё не зависит ([бэклог](../../DECISIONOPS_BACKLOG.md)).

## Offline-доказательства

- Три ответа Superset по `weekly-margin`, записанные учётной записью `inventory_reader`, —
  фикстура `tests/fixtures/superset_inventory/example_weekly-margin.json` (обрезана по тем же
  правилам, что фикстуры D6; значений секретов стенда в ней 0).
  `test_example_snapshot_is_the_inventory_of_the_stand_answers` выполняет команду раздела
  «Снимок дашбордов» README примера (`--replace` поверх закоммиченного снимка) и новый прогон без
  `--replace`. Оба дают каталог `dashboards/` примера байт в байт.
- `test_rest_snapshot_refresh_to_pr_comment` — от HTTP-ответов Superset через `--map`, exit 0 и
  `--replace` с exit 3 до `verify --dashboards … --pr-comment`. Прогон PR не создаёт клиента
  Superset. Ref отказанного и ref без сопоставления видны как `bi.spec_missing`.
  Метаданные `inventory.json` (`changed_on`, `reader_version`) вердикт и evidence не меняют.
- `tests/test_inventory_snapshot_workflow.py` — границы шаблона: событие, ветка, SHA, секреты,
  права, коды выхода; ни `action.yml`, ни демонстрационный workflow не ссылаются на учётные
  данные Superset.
- Мутанты: 25 дешёвых неверных реализаций заявлений E3 — удаление прежнего снимка вместо
  переноса, слияние старого и нового, чужие файлы, ссылки, жёсткие ссылки, reparse points Windows,
  дубликаты ключей map, обращение к Superset до проверки map, шаблон на `pull_request`, с
  правом записи, по ветке вместо SHA, с секретами на весь job; по ревью r1 — шаг инвентаря в
  каталоге checkout `inputs`, inline-Python без `-I`, версия ref длиннее лимита `int()`.
  Убиты все 25. Первый прогон оставил живым дубликат ключа в map — тест проверял его только на
  пустом списке; исправлено.
- Гейт: полный pytest на Mac (`--with duckdb`) — 13528 passed, 30 skipped; black, ruff,
  mypy strict linux и win32 — чисто. Ревью Astra xhigh: r1 «принять с правками» (четыре
  P2, закрыты: шаг с секретами и рабочий каталог, длинная версия ref, ref в ошибках map,
  оговорка об откате), r2 «принять».

## На GitHub: публикация и демонстрационный PR #108

Слово владельца 2026-11-18 — выбор «Публикуй E3, потом E4a». Версия `0.8.0`, без тега и
публикации пакета (ADR 0013, п. 5).

- **PR #106** (`e3-inventory-snapshot`, голова `af93ee4`) — 16 проверок GitHub зелёные, слит
  merge-коммитом `cbf2994`. Демонстрационный workflow прошёл и на нём, ещё с пином E2
  `6cbc7dc`: `pass_with_advisory`, 0 / 0 / 7 / 15.
- **PR #107** (`e3-pin`, слит `7ca6237`) — шаблон снимка (`INVENTORY_SHA`) и Action
  демонстрационного workflow закреплены на `af93ee4`; тест держит оба пина одним коммитом.
- **PR #108** (`e3-demo-pr`) не сливается. Исходы записаны заранее — таблица
  [примера](../../../examples/decisionops-gate/README.md); перед каждым push локальный
  `decision verify` на тех же входах дал тот же вердикт и те же счётчики. Base — пример из
  `main` `7ca6237`; `dashboards` — снимок примера.

| | Прогон 1 | Прогон 2 |
|---|---|---|
| Коммит головы | `fbfd6c0`: комментарий YAML в `model.yaml` | `c4997b3`: `gross_margin_pct` `time.timezone` `Europe/Moscow` → `UTC` |
| `pull_request.base.sha` | `7ca6237` | `7ca6237` |
| Action | `brownjuly2003-code/Auto_BI@af93ee4…`, uv 0.8.23 | тот же |
| Workflow run | `44359744528` | `44359980051` |
| Записано заранее | `yaml-comment`: `pass_with_advisory`, 0, `success` | `timezone-change`: `block`, 4, `failure` |
| Вердикт | `pass_with_advisory` | `block` |
| Код выхода гейта | 0 | 4 |
| Статус шага гейта (`fail-on: block`) | `success` | `failure` |
| Статус job | `success` | `failure` |
| Checks block / review / advisory / всего | 0 / 0 / 7 / 15 | 1 / 1 / 7 / 15 |
| Run гейта | `run-d876cd78cd433d80` | `run-ef964cc8a6ce7f3c` |
| Evidence digest | `sha256:5c4827d8e9e320e399e6156830c7429a7c2916b6fcc94bb97b208cbbe5c571d3` | `sha256:03113fdd2c30fd7f2658ddccce04b9a0e2f410cd50d54516b6caedb8478c308a` |
| Семантика base | `sha256:d017cca0ab3352e2b139f1a43d3ee3829bd761cd9d0387d8619870f9a09e51e2` | та же |
| Семантика candidate | та же, что base | `sha256:524d638e4aef23edaf16534bb739d80f101f44c28030d638b11b6263cfc71996` |
| Артефакт | `decisionops-pr-108-44359744528-1`, 13 896 B | `decisionops-pr-108-44359980051-1`, 14 954 B |
| Артефакт хранится до | 2026-12-02T17:32:32Z | 2026-12-02T17:33:39Z |

- **Один комментарий.** API комментариев PR #108 возвращает один комментарий
  `github-actions[bot]`: id `6986367916`, маркер `auto-bi-decision-gate:cde7d76ca177c4b5`.
  Создан 17:32:31Z прогоном 1, обновлён 17:33:38Z прогоном 2. Тело после каждого прогона —
  [`pr-comment/after-run-1.md`](pr-comment/after-run-1.md) и
  [`pr-comment/after-run-2.md`](pr-comment/after-run-2.md).
- **Затронутое.** Прогон 2 называет `metric.time_changed` по `metric://sales/gross_margin_pct`
  (поднят из review: от метрики зависит governed-продукт) и дашборд снимка
  `dashboard://weekly_margin@7`. Плитку он не называет: «0 delivered chart(s) named»,
  `chart.metric_unread` 2, `chart.sql_partial` 2 — граница из «Вердиктов на снимке».
- **Bundle.** Оба артефакта скачаны `gh run download` и проверены
  `auto_bi decision evidence <run> --expect-digest <digest из комментария>`: exit 0,
  `content_hashed`, verifier 139. Digest прогона 1 на bundle прогона 2 отвергнут, exit 4. Bundle
  прогона 2 лежит в [`evidence/run-ef964cc8a6ce7f3c/`](evidence/run-ef964cc8a6ce7f3c/reports/summary.md)
  байт в байт, как скачан; `tests/test_e3_github_demo_record.py` держит запись на нём.
- **`ci_local` перед push #106** на `af93ee4`: 58 passed, 2 failed, 6 uncovered. `demo-image`
  упал на сети раннера: из VM не отвечает один из четырёх адресов Fastly pypi.org
  (`151.101.64.223`), три перезапуска — так же; шаги job вручную в той же VM с pypi.org на
  доступном адресе (`docker build --add-host`) прошли, на GitHub job зелёный.
  `decisionops-gate-demo/gate` вне GitHub падает на «Check out the base», как в E2. Перед
  #107 — `ci_local --job ci/quality`: PASS.
## Не сделано

- **Шаблон workflow на GitHub не запускался:** ему нужен Superset, доступный из GitHub, и
  окружение `superset-inventory` с секретами. Закреплённый SHA и отказы проверены offline.
- **Плитка маржи на снимке не названа** — см. «Вердикты на снимке»; E4.
