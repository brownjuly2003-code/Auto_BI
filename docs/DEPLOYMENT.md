# Auto_BI — деплой в проде

Как эксплуатировать уже собранный образ/процесс `auto_bi serve` на реальном хосте:
процесс-модель, reverse-proxy/TLS, готовность для оркестратора, docker-compose пример,
бэкап SQLite, ротация jsonl-логов, чеклист секретов, восстановление после рестарта.

USER_GUIDE.md отвечает на «как пользоваться», ARCHITECTURE.md — на «как устроено», этот
файл — на «как держать в проде». Скоуп по-прежнему single-user/single-host (ARCHITECTURE
§1.1 «спроектировано для N, построено для 1») — здесь нет мультитенантности и
горизонтального масштабирования.

---

## 1. Модель процесса: ровно один, `workers=1` обязательно

`auto_bi serve` держит реестр активных диалогов в памяти процесса (`api/sessions.py`,
`SessionManager`/`ManagedSession`) и буферизует события сборки для SSE
(`GET /api/v1/sessions/{id}/events`) в том же объекте. Это **намеренно process-local**:
долговечная запись живёт в Store, а реестр — нет.

Следствие: **нельзя** поднимать больше одного экземпляра процесса за один и тот же
эндпоинт — ни `uvicorn --workers N` (флаг сознательно не выведен в CLI — это не упущение),
ни несколько реплик за одним балансировщиком без sticky-routing (которого тут нет). Второй
воркер/реплика не видит сессии первого: `POST /api/v1/sessions` на воркере A и следующий
`GET .../events` на воркере B — это гарантированный `404 UnknownSession` и разорванный SSE.

- Один `auto_bi serve` процесс на деплой. Масштабирование — только вертикальное (больше
  CPU/RAM хосту), не горизонтальное.
- Рестарт процесса безопасен для истории (specs/builds/llm_calls/trace_events — в Store),
  но роняет все диалоги, находящиеся в процессе (см. §10).
- Полный resume сессий после рестарта — размеченный опциональный трек (`X-4` в
  `plan.md`), не требуется для этого скоупа.

---

## 2. Deployment profiles (`AUTO_BI_PROFILE`)

`serve` проверяет комбинацию флагов **до** bind сокета. Профиль задаётся
`AUTO_BI_PROFILE=local|demo|production` (default `local`). Неизвестное значение
логируется как warning и трактуется как `local`.

| Профиль | Когда | Что валидируется |
|---|---|---|
| `local` | CLI, тесты, разработка | без hard-fail; warning если `SEND_SAMPLES=true` |
| `demo` | публичный Space / demo | auto-only **или** (`REQUIRE_LLM_READY` + session/work/LLM budget); samples off в auto-only |
| `production` | боевой single-host | auth on, `AUTH_COOKIE_SECURE=true`, `BI_CONNECTION_STRICT=true`, `SEND_SAMPLES=false`, `ALLOW_INSECURE_REMOTE=false`, не demo_auto_only, non-default CH/Superset/admin secrets, work/session/LLM limits, `RETENTION_ENABLED=true` |

Код: `auto_bi/deployment_profile.py`. Матрица allow/deny — `tests/test_deployment_profile.py`.

**Минимальный production `.env` (фрагмент):**

```bash
AUTO_BI_PROFILE=production
AUTO_BI_AUTH_ENABLED=true
AUTO_BI_ADMIN_PASSWORD=<strong-secret>   # или AUTO_BI_AUTH_USERS_FILE=...
AUTO_BI_AUTH_COOKIE_SECURE=true
AUTO_BI_BI_CONNECTION_STRICT=true
AUTO_BI_SEND_SAMPLES=false
AUTO_BI_ALLOW_INSECURE_REMOTE=false
AUTO_BI_CH_PASSWORD=<strong-secret>
AUTO_BI_SUPERSET_PASSWORD=<strong-secret>
AUTO_BI_WORK_RATE_ENABLED=true
AUTO_BI_RETENTION_ENABLED=true
# рекомендуется:
AUTO_BI_METRICS_ENABLED=true
AUTO_BI_LLM_BUDGET_ENABLED=true
AUTO_BI_LLM_BUDGET_DAY_MAX_CALLS=500
```

Небезопасный production profile завершается с exit 2 **до** запуска uvicorn и печатает
список нарушений.

**Локальный docker-compose стенд (ClickHouse + Superset).** Порты по умолчанию
привязаны к loopback (`127.0.0.1:8123`, `127.0.0.1:8088`) — соседняя машина в LAN/VPN
не видит CH/Superset с local-only defaults. Обычный старт:

```bash
docker compose up -d
```

Явная публикация на все интерфейсы — только через override и **заданные** секреты
(compose откажет, если плейсхолдеры не заменены):

```bash
export CH_ADMIN_PASSWORD=...
export AUTO_BI_CH_PASSWORD=...
export SUPERSET_SECRET_KEY=...
export AUTO_BI_SUPERSET_PASSWORD=...
docker compose -f docker-compose.yml -f docker-compose.publish.yml up -d
```

Предпочтительнее SSH-туннель/VPN, а не LAN-publish.

---

## 3. Запуск процесса

**Docker — готовый образ из GHCR (после того, как вырезан хотя бы один тег `vX.Y.Z` —
`release.yml`, S10) или сборка локально:**

> **Релизный preflight (P1-7).** Тег `vX.Y.Z` стартует `release.yml` только после
> job `preflight`: версия тега = `pyproject.toml [project].version` = `auto_bi.__version__`,
> `CHANGELOG.md` — непустая секция `## [<версия>]`, sdist+wheel проходят `twine check` и
> clean-install smoke. `pypi` публикует **ровно** артефакты preflight (artifact, без
> пересборки). Логика — `scripts/release_preflight.py` (офлайн-тесты).
>
> **Promotion order (plan_sol шаг 6 / P1-4).** После preflight параллельно:
> `image-security` (local build → **Trivy до push** → image SBOM → push только
> `:version` → attest), `pypi`, `provenance` (sdist/wheel). Job **`finalize`**
> (нужны все три зелёные): retag **`:latest`** с уже просканированного digest,
> GitHub Release с source SBOM + **image SBOM**. Mutable `:latest` и Release
> **не** появляются до security gates. Job `release-status` (always) падает при
> любом partial.
>
> **Partial release residual.** Если `pypi=success`, а `image-security` упал —
> wheel уже на PyPI (unpublish вручную), но `:latest` и GitHub Release **не**
> создаются. Обратный partial (image ok, pypi fail) оставляет `:version` в GHCR
> без `:latest`/Release. Полный успех = зелёный `finalize`.
>
> **Supply-chain.** SLSA provenance (image + sdist/wheel), PEP 740 на PyPI,
> SBOM source (`pyproject`+`uv.lock`) и runtime image (OS+Python) — ассеты Release.
>
> **Ручной аппрув PyPI (опционально).** `environment: pypi` → Required reviewers.
> Пока `pypi` ждёт аппрува, `image-security` может уже запушить `:version`;
> `:latest`/Release всё равно ждут `finalize` (и аппрува pypi). Solo: см. §11.

```bash
# вариант A: тег уже опубликован в GHCR — просто стянуть (замените версию на нужный тег)
docker pull ghcr.io/brownjuly2003-code/auto_bi:X.Y.Z   # или :latest — последний тег

# вариант B: собрать образ самому из текущего дерева (до первого тега или с локальными патчами)
docker build -t auto_bi .
```

```bash
docker run -d --name auto_bi \
  -p 8200:8200 \
  --env-file .env \
  -e AUTO_BI_ALLOW_INSECURE_REMOTE=true \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  auto_bi \
  auto_bi serve --host 0.0.0.0 --port 8200 --log-format json --log-level INFO
```

(Замените `auto_bi` на `ghcr.io/brownjuly2003-code/auto_bi:X.Y.Z`, если тянули по варианту A.)
Дефолтный `CMD` в образе (`auto_bi serve --host 0.0.0.0 --port 8200`) уже подходит для
контейнера — переопределяйте команду только чтобы добавить `--log-format json` (см. §8) или
сменить `--log-level`. Версия запущенного образа проверяется без входа в контейнер: `GET
/api/v1/health` возвращает поле `version`.

**P0-3 fail-closed remote bind.** `serve --host 0.0.0.0` с `AUTO_BI_AUTH_ENABLED=false` и без
демо-профиля (`AUTO_BI_DEMO_AUTO_ONLY`) **отказывается стартовать**, пока нет явного
`AUTO_BI_ALLOW_INSECURE_REMOTE=true` (доверие к сети) или включённого auth. Для локальной
разработки биндитесь на `127.0.0.1` (дефолт CLI) — флаг не нужен. Публичный HF-demo слушает
`127.0.0.1` за nginx внутри контейнера. На проде предпочтительнее `AUTO_BI_PROFILE=production`
(+ auth), а не insecure-флаг. Дополнительно: `AUTO_BI_MAX_CONCURRENT_BUILDS` (default 2) и
`AUTO_BI_WORK_RATE_*` (форсируется в demo) ограничивают дорогие auto/approve/insights.
См. §2 (deployment profiles).

**Без Docker (`uv`):**

```bash
# local (default host is already 127.0.0.1)
uv run auto_bi serve --port 8200 --log-format json --log-level INFO
# trusted LAN only — explicit consent:
AUTO_BI_ALLOW_INSECURE_REMOTE=true uv run auto_bi serve --host 0.0.0.0 --port 8200
```

**Обязательно переживающие пересборку/рестарт volume'ы:**

| Путь (по умолчанию) | Что там | Переменная |
|---|---|---|
| `data/auto_bi.sqlite` | Store: sessions/specs/builds/llm_calls/dm_change_requests/trace_events/users/auth_tokens | `AUTO_BI_STORE_PATH` |
| `logs/llm_calls.jsonl` | построчный лог метаданных LLM-вызовов (hash промпта/размеры/latency/статус — НЕ сырые промпты; Anthropic/Mistral/GraceKelly) | — (путь зашит в клиентах, см. §8) |

Без этих двух volume-маунтов каждый `docker run`/пересоздание контейнера тихо теряет всю
историю — не только бэкап (§7) становится бессмысленным, но и наблюдаемость/трейс сессий.

`semantic/model.yaml` уже копируется в образ (`Dockerfile`); если модель правится через
enrichment UI (fields-first) на живом проде, а не пересборкой образа — смонтируйте её тоже
как volume, иначе правки теряются при следующем деплое.

---

## 4. Reverse-proxy + TLS

Процесс сам TLS не терминирует — это задача proxy перед ним. Два примера ниже покрывают
основной эндпоинт (`/`, `/api/v1/*`) и обязательно правильно проксируют SSE
(`/api/v1/sessions/{id}/events`) — без этого билд-лог в UI просто не дойдёт до браузера.

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name auto-bi.example.com;

    ssl_certificate     /etc/letsencrypt/live/auto-bi.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/auto-bi.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8200;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # F-2: без этих заголовков per-IP квоты (login-лимитер, LLM-квота O-2) видят
        # адрес прокси вместо клиента и вырождаются в один общий bucket на всех.
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;

        # SSE (/api/v1/sessions/*/events): без этого nginx буферизует весь ответ и UI не
        # увидит билд-лог, пока сборка не закончится целиком.
        proxy_buffering off;
        proxy_set_header X-Accel-Buffering no;
        proxy_set_header Connection "";
        proxy_read_timeout 300s;  # сборка может занять больше стандартных 60с
    }
}
```

**Caddy (проще, TLS автоматом):**

```
auto-bi.example.com {
    reverse_proxy 127.0.0.1:8200 {
        flush_interval -1   # стримить сразу, не буферизовать — нужно для SSE
    }
}
```

**Гоча per-IP квот за proxy'ем (F-2):** login-лимитер (B-3) и LLM-квота (O-2) ключуются по
IP клиента (`request.client`). За reverse-proxy каждый запрос приходит с адреса прокси —
без проброса реального IP все клиенты складываются в ОДИН bucket: один агрессор лочит всех,
а сама квота обходится сменой прокси-пути. Что нужно: (1) прокси шлёт `X-Forwarded-For`
(nginx-пример выше; Caddy делает это сам), (2) uvicorn доверяет этим заголовкам от адреса
прокси — `auto_bi serve` включает `proxy_headers` всегда, но доверяет по умолчанию только
loopback: для same-host прокси (`127.0.0.1` → `127.0.0.1:8200`) этого достаточно, для
контейнерного прокси (compose/k8s, §6) выставьте `AUTO_BI_FORWARDED_ALLOW_IPS` — адрес(а)
прокси через запятую, либо `*`, если порт приложения доступен ТОЛЬКО прокси (внутренняя
compose-сеть без published port). `*` при публично доступном порте приложения — дыра:
любой клиент подделает свой IP одним заголовком.

**Гоча Secure-cookie за proxy'ем (важно):** авто-эвристика `AUTO_BI_AUTH_COOKIE_SECURE`
(USER_GUIDE §7, ARCHITECTURE §4/B-2) решает по **`--host`, с которым запущен сам процесс**,
а не по тому, реально ли трафик снаружи идёт по HTTPS. Типичный прод-паттерн — `auto_bi
serve --host 127.0.0.1` за локальным nginx/Caddy — эвристика увидит loopback-хост и
посчитает это локальной разработкой, отключив `Secure`, хотя снаружи всё HTTPS. **В любом
деплое за reverse-proxy выставляйте `AUTO_BI_AUTH_COOKIE_SECURE=true` явно** (если
`AUTO_BI_AUTH_ENABLED=true`) — не полагайтесь на эвристику, она рассчитана на голый локальный
запуск без proxy.

---

## 5. Готовность для оркестратора

- `GET /api/v1/health` — процесс жив (liveness).
- `GET /api/v1/ready` — глубокая готовность: store (`SELECT 1`) + DWH (`SELECT 1`) + BI
  (`healthcheck()` на Superset) гейтят `{"ok": false}`/503; LLM-проверка репортится, но не
  гейтит (ARCHITECTURE §3.11). Оба пути открыты даже при включённом auth.

`auto_bi serve` всегда собирает `/ready` с полными зависимостями (store/DWH/BI
подключаются в `cli.py::_serve` безусловно) — никаких дополнительных флагов не нужно,
`{"configured": false}` встречается только в юнит-тестах, вызывающих `create_app()` напрямую
без этих зависимостей.

Пример healthcheck для compose/systemd — см. §6.

---

## 6. Docker Compose — пример прод-запуска

Этот пример — слой «приложение + reverse-proxy». Демо-стенд ClickHouse+Superset
(`docker-compose.yml` в корне) — отдельная история для разработки/eval; в проде DWH и BI
обычно уже существуют как отдельные (не поднимаемые этим файлом) сервисы, на которые
`auto_bi` только указывает через `AUTO_BI_CH_*`/`AUTO_BI_SUPERSET_*`.

```yaml
# docker-compose.prod.yml (пример; подставьте свои DWH/BI в .env)
services:
  auto_bi:
    build: .
    container_name: auto_bi
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./semantic:/app/semantic   # если модель правится вживую через enrichment UI
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8200/api/v1/ready').status==200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s
    command: ["auto_bi", "serve", "--host", "0.0.0.0", "--port", "8200",
              "--log-format", "json", "--log-level", "INFO"]

  caddy:
    image: caddy:2-alpine
    container_name: auto_bi_caddy
    restart: unless-stopped
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on:
      auto_bi:
        condition: service_healthy

volumes:
  caddy_data:
```

`Caddyfile` — как в §4, только `reverse_proxy auto_bi:8200` (имя compose-сервиса вместо
`127.0.0.1`). В этой схеме прокси приходит НЕ с loopback (compose-сеть), поэтому в `.env`
обязательно `AUTO_BI_FORWARDED_ALLOW_IPS=*` — иначе per-IP квоты увидят адрес Caddy вместо
клиентов (F-2, §4); `*` здесь безопасен, потому что у сервиса `auto_bi` нет published
port — до него дотягивается только Caddy.

**Публичное игровое демо (P8)** живёт отдельным вариантом упаковки —
`deploy/hf-demo/` (один контейнер CH+Superset+auto_bi+nginx под Hugging Face Space,
режим `AUTO_BI_DEMO_AUTO_ONLY=true`: только авто-обзор, без LLM/ключей; Superset отдаёт
дашборды анонимно через Public-роль). Подробности и smoke-процедура —
`deploy/hf-demo/README.md`; это ДЕМО-упаковка, для прода используйте схему выше.

**Текстовый путь на демо (по требованию).** По умолчанию демо — auto-only (без LLM, нулевой
бюджет). Чтобы открыть ввод текста/полей, задайте в Space secrets `AUTO_BI_DEMO_AUTO_ONLY=false`
**и** рабочий LLM (`AUTO_BI_GRACEKELLY_URL` или Anthropic): `start-autobi.sh` тогда
подключит провайдера, ПРИНУДИТЕЛЬНО включит per-IP session-квоту
(`AUTO_BI_SESSION_RATE_ENABLED=true`, `_PER_DAY` по умолчанию 50) и
`AUTO_BI_REQUIRE_LLM_READY=true` — процесс **не стартует**, если LLM-probe падает
(иначе health показывал бы text-режим при мёртвом GraceKelly). `/health.capabilities`
описывает реально доступные режимы; post-deploy:
`python deploy/hf-demo/assert_demo_profile.py https://<space>.hf.space`.
Провайдер по умолчанию — GraceKelly (`claude-sonnet-5`); контейнер Space НЕ достучится
до `127.0.0.1` на вашей машине,
поэтому `AUTO_BI_GRACEKELLY_URL` должен указывать на ПУБЛИЧНЫЙ туннель (ngrok/cloudflared) к
запущенному GraceKelly — демо живёт, только пока ваша машина и туннель включены, и каждый запрос
анонима тратит вашу LLM-квоту. Альтернатива без туннеля — прямой Anthropic API
(`AUTO_BI_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`; Anthropic SDK входит в base image).

Session-квота (O-2) режет число *запросов*, но один запрос — это несколько обращений к провайдеру
(grounding + propose + narrate + до 3 репэйров). Провайдерный предохранитель — **бюджет LLM**
(`AUTO_BI_LLM_BUDGET_ENABLED=true`, P0-3 item 4): он считает реальные обращения и списывает бюджет
на каждой попытке, включая репэйры. Для демо задайте хотя бы дневной потолок, напр.
`AUTO_BI_LLM_BUDGET_DAY_MAX_CALLS=300` и/или `_DAY_MAX_TOKENS`/`_DAY_MAX_COST_USD` (при auth off это
единый глобальный бакет на все анонимные обращения за 24ч), плюс `_SESSION_MAX_CALLS` на один диалог.
Лимит `0` = измерение не энфорсится; при `*_MAX_COST_USD` задайте `AUTO_BI_LLM_BUDGET_PRICES`
($/1000 токенов на модель). Выключен по умолчанию — CLI/локалку/тесты не трогает (ARCHITECTURE §3.6).

Альтернатива compose — systemd-юнит на голом хосте:

```ini
# /etc/systemd/system/auto_bi.service
[Unit]
Description=Auto_BI web app
After=network.target

[Service]
WorkingDirectory=/opt/auto_bi
EnvironmentFile=/opt/auto_bi/.env
ExecStart=/opt/auto_bi/.venv/bin/auto_bi serve --host 127.0.0.1 --port 8200 --log-format json
Restart=on-failure
User=auto_bi

[Install]
WantedBy=multi-user.target
```

---

## 7. Бэкап SQLite

Store — один файл SQLite (`AUTO_BI_STORE_PATH`, по умолчанию `data/auto_bi.sqlite`),
открытый без WAL (`store/db.py` — обычный rollback-journal, одно соединение,
`check_same_thread=False`). Простое копирование файла (`cp`) во время работы процесса
рискует зацепить файл в момент записи (torn read) — используйте встроенный SQLite
online-backup, который безопасен на живой БД.

**В коде / CLI-скрипте (plan_sol step 12):** `Store.backup_to(path)`,
`Store.integrity_check()`, операторский wrapper:

```bash
uv run python scripts/store_backup.py backup data/auto_bi.sqlite \
  /backup/auto_bi/auto_bi-$(date +%Y%m%d%H%M%S).sqlite
uv run python scripts/store_backup.py check /backup/auto_bi/….sqlite
uv run python scripts/store_backup.py restore-drill data/auto_bi.sqlite
```

Эквивалент через CLI sqlite3:

```bash
mkdir -p /backup/auto_bi
sqlite3 data/auto_bi.sqlite ".backup /backup/auto_bi/auto_bi-$(date +%Y%m%d%H%M%S).sqlite"
```

Cron (ежедневно в 03:00, хранить 14 копий):

```cron
0 3 * * * cd /opt/auto_bi && uv run python scripts/store_backup.py backup data/auto_bi.sqlite /backup/auto_bi/auto_bi-$(date +\%Y\%m\%d).sqlite && find /backup/auto_bi -mtime +14 -delete
```

Автотесты: `tests/test_store_backup.py`. SLO/recovery overview — [operations/SLO.md](operations/SLO.md).

Что теряется без бэкапа: история сессий/spec'ов/билдов/LLM-вызовов/заявок владельцу DM и
пользователи auth (ARCHITECTURE §3.8) — сами дашборды в Superset/DataLens не пострадают
(Store не хранит их конфиги, только ссылки), но UI потеряет весь трейс и наблюдаемость.

**Опция для непрерывной репликации** (point-in-time recovery вместо периодических
снапшотов) — [litestream](https://litestream.io/): следит за файлом БД и стримит изменения
в S3/GCS/аналог, без изменений в коде Auto_BI (Store — обычный файл). Оправдано, если
периодического cron-снапшота недостаточно (напр. RPO меньше суток); для одиночного
инструмента cron-копии обычно хватает.

### 6.1 Retention: что растёт и как это подрезать

Три таблицы растут вместе с ИСПОЛЬЗОВАНИЕМ, а не с работой пользователя: `llm_calls`
(строка на каждый вызов провайдера), `trace_events` (строка на шаг пайплайна) и
не-`live` строки `bi_artifacts` (прошлые ревизии, которые оставляет каждая пересборка).
Свип выключен по умолчанию — удаление операционной истории необратимо:

```bash
AUTO_BI_RETENTION_ENABLED=true
AUTO_BI_RETENTION_LLM_CALLS_DAYS=90      # 0 = хранить вечно (учёт затрат)
AUTO_BI_RETENTION_TRACE_EVENTS_DAYS=30
AUTO_BI_RETENTION_BI_ARTIFACTS_DAYS=30   # только superseded/deleted строки
AUTO_BI_RETENTION_SWEEP_HOURS=6
```

Свип идёт на старте `serve` и далее раз в `SWEEP_HOURS`. Что он **не трогает**:
`sessions`, `messages`, `specs`, `builds` — это работа пользователя, а не телеметрия;
и `live`-строки `bi_artifacts` — по ним живёт ownership-cleanup, их удаление осиротило бы
реальный дашборд в BI. Чистить пользовательскую историю — только осознанно и вручную.

SQLite не отдаёт место ОС после DELETE: файл не уменьшится, пока не сделать `VACUUM`
(блокирующая операция — на остановленном процессе или в окно обслуживания).

### 6.2 Prometheus-метрики

```bash
AUTO_BI_METRICS_ENABLED=true      # GET /api/v1/metrics, text exposition
```

Выключено по умолчанию: цифры глобальные (все билды, весь LLM-spend), поэтому при
`AUTH_ENABLED=true` эндпоинт доступен **только админу** — скрейперу нужен bearer-токен
(`authorization` в scrape-конфиге). Выключенный эндпоинт отвечает 404, а не 403.

Что отдаёт: `auto_bi_builds_total{status}`, `auto_bi_builds_in_flight`,
`auto_bi_build_slots_total`, `auto_bi_dwh_queries_total` + `auto_bi_dwh_query_seconds_total`,
`auto_bi_llm_calls_total{status}`, `auto_bi_llm_tokens_total{model,direction}`,
`auto_bi_llm_cost_usd_total` (по той же прайс-таблице, что и бюджетный guard — незалистанная
модель тарифицируется в 0), `auto_bi_store_rows{table}` — по последней видно, работает ли
retention. Счётчики процесса (`in_flight`, `dwh_*`) обнуляются при рестарте — это
нормальный counter reset, Prometheus его обрабатывает.

---

## 8. Ротация `logs/*.jsonl`

`logs/llm_calls.jsonl` — построчный append-лог метаданных вызовов LLM: hash промпта,
размеры, latency, статус — сырые промпты/ответы туда НЕ пишутся
(`llm/anthropic.py`/`llm/mistral.py`/`llm/gracekelly.py`, путь зашит по умолчанию, встроенной ротации/лимита
размера нет). Это дубль того, что уже надёжно живёт в Store (`llm_calls`, наблюдаемость в UI
— USER_GUIDE §5) в структурированном виде — ротация/удаление старых jsonl-файлов не теряет
агрегаты и трейс, только построчные записи метаданных.

`logrotate`:

```
# /etc/logrotate.d/auto_bi
/opt/auto_bi/logs/*.jsonl {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```

`copytruncate` обязателен — процесс держит файл открытым на дозапись (`Path.open("a")` в
клиентах), обычный `rotate`+`postrotate kill -HUP` тут не переоткрывает файл.

Логи самого приложения/uvicorn (stdout, `--log-format json` — ARCHITECTURE §3.11) — это
зона оркестратора (docker log driver, journald, `docker compose logs`), не `logrotate`;
этот файл их не касается.

---

## 9. Чеклист секретов перед деплоем

- `.env` не в git (уже в `.gitignore`) — перед первым пушем с новой машины проверить
  `git check-ignore .env`.
- Все `change_me`-плейсхолдеры из `.env.example` заменены реальными значениями:
  `ANTHROPIC_API_KEY`, `AUTO_BI_CH_PASSWORD`, `AUTO_BI_SUPERSET_PASSWORD`, при auth —
  `AUTO_BI_ADMIN_PASSWORD`, при v2/Greenplum — `AUTO_BI_GP_PASSWORD`.
- Права на файлы: `.env`, `data/auto_bi.sqlite` (хэши токенов/паролей, но всё равно не
  публичный файл), `logs/*.jsonl` (может нести значения данных из DM, если
  `AUTO_BI_SEND_SAMPLES=true` — ARCHITECTURE §4; default is `false`) — `chmod 600` / непривилегированный
  пользователь в контейнере.
- `AUTO_BI_PROFILE=production` (или `demo`) согласован с флагами §2 — `serve` откажется
  стартовать при небезопасной комбинации.
- `AUTO_BI_AUTH_COOKIE_SECURE=true` выставлен явно за любым reverse-proxy (см. §4) —
  не полагаться на авто-эвристику по `--host`.
- Если `AUTO_BI_AUTH_ENABLED=true`: `AUTO_BI_AUTH_USERS_FILE` вне VCS — плейнтекст-пароли в
  нём реальный секрет до хэширования при старте (USER_GUIDE §7).
- Роль DWH (`AUTO_BI_CH_USER`/`AUTO_BI_GP_USER`) — read-only, не переиспользован admin-креды
  (ARCHITECTURE §4).
- Сервисный аккаунт BI ограничен папкой/workspace «Auto_BI», не суперюзер, если это
  возможно на стороне BI.
- Перед публичным демо/деплоем — `AUTO_BI_SESSION_RATE_ENABLED=true` (+ по вкусу
  `AUTO_BI_SESSION_RATE_PER_DAY`, по умолчанию 100): без неё `POST /api/v1/sessions` и
  `/sessions/{id}/reply` не ограничены и любой вызывающий может исчерпать бюджет LLM-ключа
  (O-2, USER_GUIDE §7). Выключено по умолчанию — не ломает локальную разработку/CLI.
  Квота — in-process (см. §1), как и логин-лимитер: сбрасывается при рестарте процесса.
- Провайдерный потолок расхода LLM — `AUTO_BI_LLM_BUDGET_ENABLED=true` (P0-3 item 4): session-квота
  O-2 гейтит запросы, но не видит фактических обращений к провайдеру (один запрос = grounding +
  propose + narrate + до 3 репэйров). Бюджет списывается на КАЖДОЙ попытке (включая репэйры) по
  вызовам/токенам/стоимости/времени, на сессию и на актора/24ч; агрегат — из леджера `llm_calls`
  (переживает рестарт, в отличие от in-process квот). Задайте нужные `AUTO_BI_LLM_BUDGET_*` (0 =
  без лимита по измерению). Выключен по умолчанию.
- Квоты за прокси реально per-IP, а не один общий bucket (F-2, §4): прокси шлёт
  `X-Forwarded-For`, и если он не на loopback (compose/k8s) — выставлен
  `AUTO_BI_FORWARDED_ALLOW_IPS` (адреса прокси; `*` только когда порт приложения не
  опубликован наружу). Проверка: залогируйте/дерните `/api/v1/auth/me` с двух внешних
  адресов и убедитесь, что 429 одного клиента не лочит второго.

---

## 10. После рестарта / восстановление

`Store.reap_stuck_builds()` вызывается при каждом старте `auto_bi serve` (S07) —
сессии, застрявшие в `building` из-за убитого предыдущего процесса, автоматически получают
синтетическую `failed`-запись, без ручных шагов.

Диалоги **переживают рестарт** (X-4): промах in-memory реестра лениво регидрирует сессию из
Store — фаза диалога (уточнения/превью/собрано), текущий spec, статус билда и ссылка на
дашборд восстанавливаются по её durable-записи, так что правки словами и пересборка работают
на сессии, созданной прошлым процессом. Не восстанавливаются только регенерируемые следующим
ходом вещи (вердикты Advisor, grounding report). Билд, оборванный рестартом, воскресает как
`failed` — повторная кнопка «Собрать» пересобирает тот же одобренный spec. Подробнее —
ARCHITECTURE §3.15.

---

## 11. GitHub repository protection (plan_sol step 5)

Публичный репозиторий должен закрывать прямой merge/push в `main` и перезапись
release-тегов. **Локальный scaffolding уже в git:**

| Артефакт | Назначение |
|---|---|
| [`.github/CODEOWNERS`](../.github/CODEOWNERS) | владелец review (solo: `@brownjuly2003-code`) |
| [`.github/pull_request_template.md`](../.github/pull_request_template.md) | security / data / docs / release checklist |
| [`.github/dependabot.yml`](../.github/dependabot.yml) | version updates (uv + actions + docker), minor/patch groups |
| [`scripts/apply_github_protection.py`](../scripts/apply_github_protection.py) | dry-run / apply rulesets + Dependabot security updates |

Обязательные check-run names (должны совпадать с `name:` job в workflows):

1. `Lint, format & tests (offline)`
2. `Dependency audit (pip-audit)`
3. `Docker image build (drift check)`
4. `Integration (ClickHouse + Superset stand)`
5. `gitleaks`
6. `analyze (python)`

Сверка имён — `tests/test_github_protection.py`.

### Внешние операции (только с явным разрешением оператора)

```bash
# только план
python scripts/apply_github_protection.py
python scripts/apply_github_protection.py --status

# мутация GitHub (rulesets + security updates) — GATE
python scripts/apply_github_protection.py --apply
```

Что делает `--apply`:

- ruleset **main protection**: PR required, conversation resolution, required checks
  выше, no force-push (`non_fast_forward`), no branch delete;
- ruleset **release tags v\***: запрет update/delete/force на `refs/tags/v*`;
- включает `dependabot_security_updates` на репозитории.

### Solo-maintainer residuals (намеренно)

- `require_code_owner_review` и `required_approving_review_count` **выключены** в
  ruleset payload — иначе единственный владелец не смержит свой PR. CODEOWNERS всё
  равно документирует ownership; включить review enforcement, когда появится второй
  reviewer.
- Environment `pypi`: **`prevent_self_review=false`** — иначе solo release deadlock.
  `can_admins_bypass` на environment сейчас true; сужать после появления второго
  человека.
- Coverage badge commit-back в CI **soft-skip** при отказе push (ruleset); badge может
  отставать, quality job не краснеет.
- Открытые Dependabot PR разбираются **малыми совместимыми группами** (не bulk-merge
  major actions major+uv в одном окне).

### Аварийный bypass

Постоянных `bypass_actors` в ruleset нет. Временный обход — UI ruleset
`enforcement: disabled` / bypass на конкретный PR, с записью в CHANGELOG/ops note и
немедленным возвратом `active`. Не force-push в `main` и не переписывать `v*` tags.

---

См. также: [ARCHITECTURE.md](ARCHITECTURE.md) §3.8 (Store), §3.11 (Ops-hardening), §3.15
(Session-resume), §4
(Безопасность); [USER_GUIDE.md](USER_GUIDE.md) §4 (Web UI/логи/готовность), §6
(конфигурация), §7 (auth/RBAC); [SECURITY.md](../SECURITY.md).
