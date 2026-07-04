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
  но роняет все диалоги, находящиеся в процессе (см. §9).
- Полный resume сессий после рестарта — размеченный опциональный трек (`X-4` в
  `plan.md`), не требуется для этого скоупа.

---

## 2. Запуск процесса

**Docker — готовый образ из GHCR (после того, как вырезан хотя бы один тег `vX.Y.Z` —
`release.yml`, S10) или сборка локально:**

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
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  auto_bi \
  auto_bi serve --host 0.0.0.0 --port 8200 --log-format json --log-level INFO
```

(Замените `auto_bi` на `ghcr.io/brownjuly2003-code/auto_bi:X.Y.Z`, если тянули по варианту A.)
Дефолтный `CMD` в образе (`auto_bi serve --host 0.0.0.0 --port 8200`) уже подходит для
контейнера — переопределяйте команду только чтобы добавить `--log-format json` (см. §7) или
сменить `--log-level`. Версия запущенного образа проверяется без входа в контейнер: `GET
/api/v1/health` возвращает поле `version`.

**Без Docker (`uv`):**

```bash
uv run auto_bi serve --host 0.0.0.0 --port 8200 --log-format json --log-level INFO
```

**Обязательно переживающие пересборку/рестарт volume'ы:**

| Путь (по умолчанию) | Что там | Переменная |
|---|---|---|
| `data/auto_bi.sqlite` | Store: sessions/specs/builds/llm_calls/dm_change_requests/trace_events/users/auth_tokens | `AUTO_BI_STORE_PATH` |
| `logs/llm_calls.jsonl` | построчный лог сырых LLM-вызовов (Anthropic/GraceKelly) | — (путь зашит в клиентах, см. §7) |

Без этих двух volume-маунтов каждый `docker run`/пересоздание контейнера тихо теряет всю
историю — не только бэкап (§6) становится бессмысленным, но и наблюдаемость/трейс сессий.

`semantic/model.yaml` уже копируется в образ (`Dockerfile`); если модель правится через
enrichment UI (fields-first) на живом проде, а не пересборкой образа — смонтируйте её тоже
как volume, иначе правки теряются при следующем деплое.

---

## 3. Reverse-proxy + TLS

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

**Гоча Secure-cookie за proxy'ем (важно):** авто-эвристика `AUTO_BI_AUTH_COOKIE_SECURE`
(USER_GUIDE §7, ARCHITECTURE §4/B-2) решает по **`--host`, с которым запущен сам процесс**,
а не по тому, реально ли трафик снаружи идёт по HTTPS. Типичный прод-паттерн — `auto_bi
serve --host 127.0.0.1` за локальным nginx/Caddy — эвристика увидит loopback-хост и
посчитает это локальной разработкой, отключив `Secure`, хотя снаружи всё HTTPS. **В любом
деплое за reverse-proxy выставляйте `AUTO_BI_AUTH_COOKIE_SECURE=true` явно** (если
`AUTO_BI_AUTH_ENABLED=true`) — не полагайтесь на эвристику, она рассчитана на голый локальный
запуск без proxy.

---

## 4. Готовность для оркестратора

- `GET /api/v1/health` — процесс жив (liveness).
- `GET /api/v1/ready` — глубокая готовность: store (`SELECT 1`) + DWH (`SELECT 1`) + BI
  (`healthcheck()` на Superset) гейтят `{"ok": false}`/503; LLM-проверка репортится, но не
  гейтит (ARCHITECTURE §3.11). Оба пути открыты даже при включённом auth.

`auto_bi serve` всегда собирает `/ready` с полными зависимостями (store/DWH/BI
подключаются в `cli.py::_serve` безусловно) — никаких дополнительных флагов не нужно,
`{"configured": false}` встречается только в юнит-тестах, вызывающих `create_app()` напрямую
без этих зависимостей.

Пример healthcheck для compose/systemd — см. §5.

---

## 5. Docker Compose — пример прод-запуска

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

`Caddyfile` — как в §3, только `reverse_proxy auto_bi:8200` (имя compose-сервиса вместо
`127.0.0.1`).

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

## 6. Бэкап SQLite

Store — один файл SQLite (`AUTO_BI_STORE_PATH`, по умолчанию `data/auto_bi.sqlite`),
открытый без WAL (`store/db.py` — обычный rollback-journal, одно соединение,
`check_same_thread=False`). Простое копирование файла (`cp`) во время работы процесса
рискует зацепить файл в момент записи (torn read) — используйте встроенный SQLite
online-backup, который безопасен на живой БД:

```bash
mkdir -p /backup/auto_bi
sqlite3 data/auto_bi.sqlite ".backup /backup/auto_bi/auto_bi-$(date +%Y%m%d%H%M%S).sqlite"
```

Cron (ежедневно в 03:00, хранить 14 копий):

```cron
0 3 * * * cd /opt/auto_bi && sqlite3 data/auto_bi.sqlite ".backup /backup/auto_bi/auto_bi-$(date +\%Y\%m\%d).sqlite" && find /backup/auto_bi -mtime +14 -delete
```

Что теряется без бэкапа: история сессий/spec'ов/билдов/LLM-вызовов/заявок владельцу DM и
пользователи auth (ARCHITECTURE §3.8) — сами дашборды в Superset/DataLens не пострадают
(Store не хранит их конфиги, только ссылки), но UI потеряет весь трейс и наблюдаемость.

**Опция для непрерывной репликации** (point-in-time recovery вместо периодических
снапшотов) — [litestream](https://litestream.io/): следит за файлом БД и стримит изменения
в S3/GCS/аналог, без изменений в коде Auto_BI (Store — обычный файл). Оправдано, если
периодического cron-снапшота недостаточно (напр. RPO меньше суток); для одиночного
инструмента cron-копии обычно хватает.

---

## 7. Ротация `logs/*.jsonl`

`logs/llm_calls.jsonl` — построчный append-лог сырых вызовов LLM
(`llm/anthropic.py`/`llm/gracekelly.py`, путь зашит по умолчанию, встроенной ротации/лимита
размера нет). Это дубль того, что уже надёжно живёт в Store (`llm_calls`, наблюдаемость в UI
— USER_GUIDE §5) в структурированном виде — ротация/удаление старых jsonl-файлов не теряет
агрегаты и трейс, только сырые построчные записи.

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

## 8. Чеклист секретов перед деплоем

- `.env` не в git (уже в `.gitignore`) — перед первым пушем с новой машины проверить
  `git check-ignore .env`.
- Все `change_me`-плейсхолдеры из `.env.example` заменены реальными значениями:
  `ANTHROPIC_API_KEY`, `AUTO_BI_CH_PASSWORD`, `AUTO_BI_SUPERSET_PASSWORD`, при auth —
  `AUTO_BI_ADMIN_PASSWORD`, при v2/Greenplum — `AUTO_BI_GP_PASSWORD`.
- Права на файлы: `.env`, `data/auto_bi.sqlite` (хэши токенов/паролей, но всё равно не
  публичный файл), `logs/*.jsonl` (может нести значения данных из DM, если
  `AUTO_BI_SEND_SAMPLES=true` — ARCHITECTURE §4) — `chmod 600` / непривилегированный
  пользователь в контейнере.
- `AUTO_BI_AUTH_COOKIE_SECURE=true` выставлен явно за любым reverse-proxy (см. §3) —
  не полагаться на авто-эвристику по `--host`.
- Если `AUTO_BI_AUTH_ENABLED=true`: `AUTO_BI_AUTH_USERS_FILE` вне VCS — плейнтекст-пароли в
  нём реальный секрет до хэширования при старте (USER_GUIDE §7).
- Роль DWH (`AUTO_BI_CH_USER`/`AUTO_BI_GP_USER`) — read-only, не переиспользован admin-креды
  (ARCHITECTURE §4).
- Сервисный аккаунт BI ограничен папкой/workspace «Auto_BI», не суперюзер, если это
  возможно на стороне BI.

---

## 9. После рестарта / восстановление

`Store.reap_stuck_builds()` вызывается при каждом старте `auto_bi serve` (S07) —
сессии, застрявшие в `building` из-за убитого предыдущего процесса, автоматически получают
синтетическую `failed`-запись, без ручных шагов.

Диалоги, которые были активны в памяти (`ManagedSession`-реестр), рестарт **не переживают** —
пользователь увидит, что сессии больше нет, и начнёт новую через UI/API; вся durable-история
(specs/builds/trace) остаётся доступной через `GET /api/v1/sessions/{id}/trace` независимо от
реестра. Полный resume диалога после рестарта — опциональный трек `X-4` (`plan.md`), в этот
скоуп не входит.

---

См. также: [ARCHITECTURE.md](ARCHITECTURE.md) §3.8 (Store), §3.11 (Ops-hardening), §4
(Безопасность); [USER_GUIDE.md](USER_GUIDE.md) §4 (Web UI/логи/готовность), §6
(конфигурация), §7 (auth/RBAC); [SECURITY.md](../SECURITY.md).
