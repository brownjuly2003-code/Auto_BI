# Phase 3.1/3.2 — DataLens self-hosted (доступ создан без Yandex Cloud)

Дата: 2026-06-13. Yandex Cloud Public API (`api.datalens.tech`) недоступен — нет аккаунта
(нужна телефон-верификация/биллинг, кредов нет и не будет). По указанию «делай сам доступ»
поднят **self-hosted open-source DataLens** (`github.com/datalens-tech/datalens`) на Mac-стенде.
Тот же кодовый формат чартов/датасетов → валиден для спайка 3.1 и адаптера 3.2; отличается
только auth/transport-шов (локальная сессия вместо IAM+org-id).

## Где и как поднято (Mac `deproject-mac`, Colima vz VM 5.786GiB, Intel x86_64 → amd64 нативно)

- Репозиторий: `~/datalens_dl` (клон `datalens-tech/datalens`, depth 1).
- Запуск: `cd ~/datalens_dl && HC=1 /usr/local/bin/docker compose up -d`
  - **полный путь `/usr/local/bin/docker`** обязателен: в non-login SSH-шелле `docker` НЕ на PATH (rc=127).
  - **`HC=1`** включает Highcharts (нужно спайку: Editor-чарты на Highcharts + нативный декартов heatmap).
- 11 сервисов, 8 образов `ghcr.io/datalens-tech/*` (ui, ui-api, control-api, data-api, us, auth,
  postgres, meta-manager, temporal).

## Версия стенда — контрактный пин (Phase 4 F7, инвариант 7)

Реверс-блобы DataLens-адаптера завязаны на конкретную версию gateway/US/charts-engine, поэтому
поддержанная версия фиксируется здесь (аналог пина Superset в `docker-compose.yml`, инвариант 7
теперь распространён на DataLens — см. ARCHITECTURE §3.5).

**Контрактные маркеры версии (на чём реверснуты и live-проверены payload'ы 3.1/3.2):**

| Маркер | Значение | Где используется |
|---|---|---|
| UI-gateway | `v4.10.4` | транспорт `/gateway/root/<svc>/<method>` (баг `validateDataset` 415 в этой версии) |
| dash zod `dataSchema` `schemeVersion` | `8` | инжектится server-side `mix/createDashboardV1`; блоб его НЕ шлёт (`build_dashboard_data`) |
| chart `shared` `version` | `"4"` | `chart_config.build_chart_shared` (закреплён unit-тестом F10) |
| Highcharts | `HC=1` | Editor-чарты на Highcharts + нативный heatmap (иначе деградация в pivot) |
| gateway-экшен `us/renameEntry` | `{entryId, name}` → 200, entryId стабилен | атомарный rebuild `_promote_to_canonical` (Phase 4 F2); прямой REST `/v1/entries/:id/rename` через UI-gateway = 404 (не проксируется) |
| Источник образов | `ghcr.io/datalens-tech/*` | клон `datalens-tech/datalens` depth=1 |

**Гэп (то, что ещё не запинено намертво):** стенд — это depth-1 клон `main`, образы тянутся по
плавающему тегу (фактически `:latest`-семантика), поэтому точные digest'ы образов в репо НЕ
зафиксированы. Чтобы закрыть гэп при следующем поднятии стенда — снять точные теги/digest'ы и
вписать их сюда:

```bash
# на Mac-стенде, стенд Up:
cd ~/datalens_dl && /usr/local/bin/docker compose images          # тег по сервису
cd ~/datalens_dl && /usr/local/bin/docker compose config | grep -E '^\s+image:'  # image-строки compose
```

**Дисциплина апгрейда (инвариант 7 для DataLens):** обновление версии стенда (новый pull / смена
тега) — ОТДЕЛЬНАЯ задача с обязательным прогоном live contract-сьюта `tests/test_datalens_contract.py`
(11 кейсов, integration-gated); расхождение блоба → правка реверс-дока + адаптера до мержа.

## КРИТИЧНО — DNS-фикс для ghcr.io (иначе pull падает)

Симптом: `failed to resolve reference "ghcr.io/...": dial tcp: lookup ghcr.io on 192.168.5.1:53:
no such host` (docker.io тянется нормально — проблема только ghcr.io). Причина: **Go-резолвер
dockerd** не резолвит ghcr.io через gvproxy (192.168.5.1), хотя glibc-резолвер VM работает
(`getent hosts ghcr.io` → ОК). Это ровно причуда [[de-mac-docker-env]] (`GODEBUG=netdns=cgo`).

Фикс (без рестарта демона/VM — Go-резолвер перечитывает resolv.conf на каждый pull):
```bash
ssh deproject-mac 'bash -lc "colima ssh -- sudo sh -c \"grep -q 8.8.8.8 /etc/resolv.conf || \
  printf \\\"nameserver 8.8.8.8\\nnameserver 1.1.1.1\\n\\\" >> /etc/resolv.conf\""'
```
После — `docker compose pull` проходит. Не персистентно через рестарт VM, НО образы кешируются,
так что повторный pull не нужен; фикс важен только при первой загрузке/обновлении образов.

## temporal/meta-manager — гонка старта

При первом `up` temporal падает (`postgres server is not available, exit`) — стартует раньше,
чем postgres стал healthy; meta-manager ждёт temporal. Лечится повтором ПОСЛЕ healthy postgres:
```bash
cd ~/datalens_dl && HC=1 /usr/local/bin/docker compose up -d temporal meta-manager
```
(temporal → healthy, meta-manager → Up). Для базового чартинга temporal/meta-manager не критичны.

## Доступ

- UI: `http://localhost:8080` на Mac, логин **admin / admin**. HC включён.
- С Windows: туннель `ssh -N -L 8090:localhost:8080 deproject-mac` → `http://127.0.0.1:8090`.
  **Логин admin/admin проверен через Playwright** → редирект на `/collections`, аутентифицированное
  приложение («Yandex DataLens open source», меню Create/Settings/Account) usable. Скриншот:
  `D:\.playwright-mcp\datalens_authenticated_collections.png`. Доступ рабочий end-to-end.
- Порт 8080 на Mac был свободен (8123=CH, 8088=Superset, 8011=GraceKelly на Windows).

## Ресурсы / сосуществование со стендом

- Чтобы освободить RAM, **остановлен `auto_bi_greenplum`** (его GP-валидация завершена; данные
  сохранены в stopped-контейнере). Вернуть: `docker start auto_bi_greenplum` (+ туннель 15433).
- DataLens-контейнеры мелкие (~100-560MiB), стек влез в VM рядом с CH+Superset, OOM не было.

## Дальше (спайк 3.1 → адаптер 3.2)

Реверс с ЖИВОГО инстанса (инвариант проекта — не угадывать): createConnection (на CH/GP-стенд) →
createDataset → **createEditorChart (Highcharts)** → createDashboard; auth локальной сессии;
сверить с capability-matrix в `2026-06-13-phase3-prep.md` (§A) и go/no-go (§A.0). Затем адаптер
`adapters/datalens/` (editor_config.py + adapter.py), шов ref `id: int|str` уже готов (S4-2).
