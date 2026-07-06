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

---

## Аутентифицированная headless-сессия для render-verify (P3, 2026-07-06)

**Задача:** скриншотить/читать глазами дашборды DataLens из headless-браузера (для P4 render-verify,
P7 демо-записи). Ранее (N1) считалось, что headless к 8090 не аутентифицируется: SPA-shell грузится,
data-API → 403. **Это оказалось НЕ auth-багом.** Форм-логин admin/admin работает end-to-end,
data-API отдаёт 200, чарты рендерятся с данными. Реальный блокер был инфраструктурный —
конкуренция за persistent-профиль браузера (ниже). Метод ниже воспроизводим и переиспользуем P4/P7.

### Рабочий метод (Playwright-MCP, persistent Edge-профиль `D:/edge-headless/Default`)

1. **Туннель** (один, не плодить — см. `mac-stand-ssh-saturation`): проверить
   `netstat -ano -p tcp | grep 8090`; если пусто — `ssh -f -N -L 8090:localhost:8080 deproject-mac`.
2. **Навигация:** `browser_navigate http://127.0.0.1:8090/`. Если сессия жива (cookie не истёк) —
   сразу `/collections`. Если нет — редирект на `/auth/signin`.
3. **Форм-логин (только если попал на `/auth/signin`):** `browser_type` в поля Username/Password
   значением `admin`/`admin` → `browser_click` кнопку «Sign in» → редирект на `/collections`.
4. **Открыть дашборд:** `browser_navigate` на `http://127.0.0.1:8090/<entryId>-<slug>` (или кликом из
   workbook). `browser_wait_for time:6` (чарты грузятся async). Верификация = скриншот + чтение
   глазами (числа/бары/линии), НЕ только отсутствие 403 в network (`dont-claim-unverified`).

### Почему persistent-профиль решает задачу (не нужен re-login каждую сессию)

Auth-cookie DataLens `auth` — **персистентный** (httpOnly, `secure=false` → работает по plain-http
127.0.0.1, `sameSite=Strict`, TTL ~240 ч ≈ **10 дней**), плюс `auth_exp` (маркер, не-httpOnly).
Persistent-профиль (`--user-data-dir=D:/edge-headless/Default` в конфиге Playwright-MCP) пишет
cookie на диск ⇒ следующая headless-сессия по тому же профилю УЖЕ залогинена, пока cookie жив.
Истёк (>10 дней) → тривиальный повтор форм-логина (шаг 3) обновляет. `sameSite=Strict` проблем не
создаёт: все data-API-вызовы (`/api/run`, `/api/dash/v1/...`, `/gateway/root/...`) same-origin.
Отдельный save/reload `storageState`-файла НЕ нужен — persistent-профиль и есть хранилище.

### ⚠️ Реальный блокер N1 — оркестровка orphan MCP-серверов за профиль (а не auth)

Симптом при `browser_navigate`/`browser_close`:
`Error: Browser is already in use for D:/edge-headless/Default, use --isolated to run multiple instances`.
Причина: от прошлых сессий остаются **живые orphan-процессы Playwright-MCP** (пары `cmd.exe → npx →
node @playwright/mcp/cli.js --user-data-dir=D:/edge-headless/Default`) + их дочерние браузеры,
держащие lock профиля (`D:/edge-headless/Default/lockfile`). Мой live MCP-сервер не может взять
профиль → любой browser-tool падает. **Это и был «403»** прошлой сессии: логин не завершался
в состоянии залоченного профиля, а не cookie/CSRF.

**Диагностика (какие node — orphan, какой — мой live):**
```bash
# все MCP-node с временем старта; самая свежая пара (по CreationDate) — обычно текущая live-сессия
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { \$_.CommandLine -like '*@playwright*' -and \$_.Name -eq 'node.exe' } | Select-Object ProcessId,ParentProcessId,CreationDate | Sort-Object CreationDate | Format-Table -AutoSize"
```
Процессы идут парами (npx-wrapper node → cli.js node; gparent второго = PID первого). Каждая пара =
один MCP-инстанс. Свежайшая пара по времени = текущая сессия; более старые = orphan прошлых сессий.

**Уборка (килль ТОЛЬКО старые пары, НЕ трогай свою свежую — иначе потеряешь browser-tools):**
kill по верхнему `cmd.exe` каждой orphan-цепочки с `//T` (снимет и дочерний браузер):
```bash
taskkill //F //T //PID <top_cmd_of_orphan_chain>   # top_cmd = ParentProcessId самого старого node в паре
```
После — `lockfile` исчезает (убитый браузер отпустил профиль), `browser_navigate` работает.
Если `lockfile` завис при мёртвом владельце — удалить руками: `rm D:/edge-headless/Default/lockfile`.
⚠️ Различай `msedge.exe`/`chrome.exe` (браузер MCP) от `msedgewebview2.exe` (чужой WebView2 рантайм —
не трогать). Дефолтный браузер Playwright-MCP = Chromium (`chrome.exe`), несмотря на имя папки.

### Гигиена завершения сессии

Закрывать свой браузер в конце (`browser_close`) — освобождает lock профиля для следующей сессии
(cookie уже на диске, ничего не теряется). Туннель 8090 можно оставить ОДИН живой для follow-on
(P4/P7). Прерванные python/node от пробников к стенду = zombie, вешают git/стенд —
`taskkill //F //IM node.exe` / `//IM python.exe //T` при явных зависаниях (плановая грабля P3).

### Скриптовый headless-verify без Playwright-MCP (P4, 2026-07-06)

Когда MCP browser-tools недоступны, тот же persistent-профиль работает из СВОЕГО
Playwright-скрипта: `chromium.launchPersistentContext('D:/edge-headless/Default', {headless:
true})` (готовый скрипт — `D:\dashboard_kit\_ab_dl_p4_verify.mjs`: форм-логин при отсутствии
`auth`-cookie + скриншот + сбор `/api/run`-статусов). Два gotcha:
1. **`code.highcharts.com` недоступен из sandboxed-шелла** (403 на любой запрос; MCP-браузер
   ходил через системный VPN — потому у P3 чарты рендерились). Фикс в скрипте: `ctx.route`
   перехватывает `https://code.highcharts.com/**` и отдаёт файлы из npm-пакета
   `highcharts@8.2.2` локально (`npm pack highcharts@8.2.2`) — те же файлы, что отдал бы CDN.
2. Проверку «залогинен ли» делать по НАЛИЧИЮ `auth`-cookie (`ctx.cookies()`), не по redirect
   на `/auth` — SPA-shell entry-страницы может загрузиться и без сессии (data-API при этом 403).

Побочно реверсировано там же: `/api/run` принимает **inline (unsaved) config** в body
(`{"config": {"data": {"shared": "<json>"}, "meta": {"stype": "metric_wizard_node"}}}` — wizard-
runner `safeConfig: true`), это используется адаптером как magnitude-проба N2; и `POST
/api/charts/v1/charts/:entryId` = update существующего чарта (тот же body, что у create).

### Верифицировано (2026-07-06)

Дашборд `zlgn1i1cug3wi` «Обзор legend-verify : Дневные продажи» (workbook Auto_BI `ra7f79yirtumb`):
все 9 `/api/run` → 200, скриншот `D:\.playwright-mcp\datalens_p3_legend_verify.png` читается глазами —
4 KPI (Выручка 236B, г/г 0,0%, Число заказов 115M, Число позиций 210M), line «Динамика по месяцам»
(Sep'24→May'26, 6–16B), bars Регион (8 ФО, 0–35B) / Категория (13 позиций, 0–20B) / Доля Формат
(3 бара, 0–0.4). Data-API 200 end-to-end, никаких 403. (Дашборд `h3y22qrn77cw0` того же workbook —
все чарты «Not found»/404: его widget/dataset-entry вычищены прошлыми сессиями; это НЕ auth и НЕ CH —
`auto_bi_clickhouse` healthy — а стейл-entry. Для render-verify брать дашборд с живыми чартами.)
Побочно виден кандидат N2: KPI «236B» = SI-локаль-единица (RU-единицы — предмет P4(c)).
