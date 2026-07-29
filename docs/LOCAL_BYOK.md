# Локальный запуск Auto_BI со своим ключом Anthropic (BYOK)

Краткая инструкция для первого локального прогона: клонируете репозиторий, поднимаете
демо-стенд ClickHouse + Superset, запускаете Auto_BI со **своим** `ANTHROPIC_API_KEY`.

**Скоуп v1:** Auto_BI + ClickHouse (DM) + Apache Superset (BI). Полное руководство
пользователя — [USER_GUIDE.md](USER_GUIDE.md); полный inventory переменных —
[ENV_REFERENCE.md](ENV_REFERENCE.md); прод и reverse-proxy — [DEPLOYMENT.md](DEPLOYMENT.md).

Контейнерный запуск самого Auto_BI (образ GHCR / `docker build`) здесь не разбирается —
см. [DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. Что понадобится

| Требование | Зачем |
|---|---|
| Git | клон репозитория |
| Python **3.12+** | runtime пакета |
| [uv](https://docs.astral.sh/uv/) | установка из `uv.lock` и `uv run` |
| Docker Engine / Docker Desktop **с Docker Compose** | демо-стенд ClickHouse + Superset |

Локальные порты (loopback):

| Порт | Сервис |
|---|---|
| `8123` | ClickHouse HTTP |
| `8088` | Apache Superset |
| `8200` | Auto_BI (`auto_bi serve`) |

---

## 2. Клон и установка

```bash
git clone https://github.com/brownjuly2003-code/Auto_BI.git
cd Auto_BI
uv sync --frozen --no-dev
```

`--frozen` ставит зависимости строго по `uv.lock`; `--no-dev` пропускает test/lint-инструменты
(тот же путь, что и в `Dockerfile` для runtime-слоя).

---

## 3. Минимальный `.env`

Скопируйте шаблон:

```bash
# POSIX
cp .env.example .env
```

```powershell
# PowerShell
Copy-Item .env.example .env
```

Отредактируйте `.env`. Для первого BYOK-прогона достаточно такого минимума
(замените плейсхолдеры):

```bash
AUTO_BI_PROFILE=local

# ClickHouse (клиент Auto_BI → DWH)
AUTO_BI_CH_HOST=localhost
AUTO_BI_CH_PORT=8123
AUTO_BI_CH_USER=auto_bi_ro
AUTO_BI_CH_PASSWORD=<your-ch-ro-password>
AUTO_BI_CH_DATABASE=dm
# как BI (Superset) видит ClickHouse внутри compose-сети
AUTO_BI_CH_HOST_FROM_BI=clickhouse
AUTO_BI_CH_PORT_FROM_BI=8123

# Superset (клиент Auto_BI → BI)
AUTO_BI_SUPERSET_URL=http://localhost:8088
AUTO_BI_SUPERSET_USER=admin
AUTO_BI_SUPERSET_PASSWORD=<your-superset-password>

# LLM — прямой Anthropic API (BYOK)
AUTO_BI_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<your-anthropic-api-key>

# top-N значений DWH во внешний LLM не уходят (безопасный default)
AUTO_BI_SEND_SAMPLES=false

# --- только для docker compose (демо-стенд) ---
# пароль admin ClickHouse (healthcheck / admin-операции init)
CH_ADMIN_PASSWORD=<your-ch-admin-password>
# секрет Flask/Superset
SUPERSET_SECRET_KEY=<your-superset-secret>
# объём синтетического fact (дефолт compose — 100M строк; для первого раза меньше)
DEMO_FACT_ROWS=1000000
```

### Согласованность паролей

Compose **создаёт** учётки при первом старте томов, а Auto_BI **подключается** к ним
теми же переменными:

| Учётка | Создаётся compose из | Клиент Auto_BI читает |
|---|---|---|
| ClickHouse `auto_bi_ro` | `AUTO_BI_CH_PASSWORD` (внутри контейнера как `AUTO_BI_RO_PASSWORD`) | `AUTO_BI_CH_PASSWORD` |
| Superset `admin` | `AUTO_BI_SUPERSET_PASSWORD` | `AUTO_BI_SUPERSET_PASSWORD` |

Одно и то же значение должно совпадать в `.env` **до** первого `docker compose up`.
Если сменить пароль в `.env` после того, как тома уже инициализированы, compose
не пересоздаст учётки сам — будут «stale credentials» (см. §8).

Секреты и ключи в git не коммитьте: `.env` в `.gitignore`.

---

## 4. Поднять зависимости (ClickHouse + Superset)

Из корня репозитория:

```bash
docker compose up -d
```

**Важно:** корневой `docker compose up -d` поднимает **только** ClickHouse и Superset.
Процесс Auto_BI этим файлом **не** стартует.

Статус:

```bash
docker compose ps
```

Оба сервиса должны стать healthy (первый старт может занять несколько минут: образ
Superset, init ClickHouse, генерация demo-fact по `DEMO_FACT_ROWS`).

Логи при зависании:

```bash
docker compose logs --tail=100 clickhouse
docker compose logs --tail=100 superset
```

---

## 5. Запустить Auto_BI

В корне репозитория (отдельный терминал, `.env` подхватится автоматически):

```bash
uv run auto_bi serve
```

По умолчанию UI и API слушают `http://127.0.0.1:8200`
(`--host 127.0.0.1`, `--port 8200`).

В репозитории уже есть `semantic/model.yaml` под демо-витрины стенда. Если файл
отсутствует или модель не подходит к вашему DWH:

```bash
uv run auto_bi introspect --output semantic/model.yaml
```

---

## 6. Проверки liveness и readiness

`/api/v1/health` — процесс жив (liveness).
`/api/v1/ready` — store + DWH (`SELECT 1`) + BI (healthcheck Superset) доступны;
LLM-проверка **репортится**, но **не** гейтит `ok` (503 только при сбое store/DWH/BI).

Для провайдера `anthropic` readiness **не** делает платный запрос к Anthropic и
**не** доказывает, что ключ принят провайдером: live-check намеренно отключён,
чтобы не тратить токены на каждый probe.

### curl (POSIX / Git Bash)

```bash
curl -sS http://127.0.0.1:8200/api/v1/health
curl -sS -i http://127.0.0.1:8200/api/v1/ready
```

Ожидание: HTTP 200 и `"ok": true` в JSON (для `/ready` при живых зависимостях).

### PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8200/api/v1/health
Invoke-WebRequest http://127.0.0.1:8200/api/v1/ready | Select-Object StatusCode, Content
```

Откройте UI: <http://127.0.0.1:8200/>.

---

## 7. Что тратит ваш LLM-аккаунт

| Действие | LLM / оплата Anthropic |
|---|---|
| Текст → сессия / уточнения / propose spec | **да** |
| Fields-first (раскладка полей → spec) | **да** |
| Правка словами после сборки | **да** |
| Авто-обзор витрины (`build --auto` / вкладка «Авто») | **нет** (детерминированно) |
| Сборка уже готового spec, SQL-guard, Advisor, адаптер BI | **нет** (детерминированно) |
| `GET /api/v1/health`, `GET /api/v1/ready` | **нет** |

`AUTO_BI_SEND_SAMPLES=false` (как в примере) не отключает LLM: он только запрещает
отправлять top-N значений колонок DWH во внешний провайдер.

---

## 8. Остановка

1. В терминале Auto_BI: **Ctrl+C**.
2. Зависимости:

```bash
docker compose down
```

Тома `clickhouse_data` и `superset_home` **сохраняются** — demo-данные и дашборды
на стенде остаются.

**Не** используйте как обычную остановку:

```bash
docker compose down -v
```

Флаг `-v` удаляет тома: локальные demo-данные ClickHouse и состояние Superset
(включая собранные дашборды на стенде) будут уничтожены.

---

## 9. Частые проблемы

| Симптом | Что проверить |
|---|---|
| `docker` / `docker compose` не найдены | Установите Docker Engine или Docker Desktop; перезапустите shell; убедитесь, что Compose v2 доступен как `docker compose`. |
| `uv` не найден | Установите [uv](https://docs.astral.sh/uv/); проверьте `uv --version`. |
| Первый `docker compose up` долгий | Нормально: pull/build образов, init CH, генерация fact (`DEMO_FACT_ROWS`). Смотрите `docker compose ps` и `logs`. Для лёгкого стенда задайте `DEMO_FACT_ROWS=1000000` **до** первого старта (или после `down -v` — с потерей томов). |
| `/api/v1/ready` → **503** | Не подняты/не healthy CH или Superset, неверные `AUTO_BI_CH_*` / `AUTO_BI_SUPERSET_*`, или store недоступен. Сверьте `docker compose ps`, пароли, `AUTO_BI_CH_HOST_FROM_BI=clickhouse`. LLM **не** валит readiness. |
| Stale / wrong Compose credentials | Пароли в `.env` сменились после первого init томов. Верните прежние значения **или** осознанно пересоздайте тома (`docker compose down -v` — удалит demo-данные) и поднимите стенд заново. |
| `Semantic model not found` | Нет `semantic/model.yaml`. Запустите `uv run auto_bi introspect --output semantic/model.yaml` (нужен живой CH). |
| Ошибки аутентификации Anthropic на text/fields | Проверьте `AUTO_BI_LLM_PROVIDER=anthropic` и `ANTHROPIC_API_KEY` (или `AUTO_BI_ANTHROPIC_API_KEY`). `/ready` при этом может оставаться 200 — он не валидирует ключ у провайдера. |

---

## 10. Дальше

- Практика UI/CLI: [USER_GUIDE.md](USER_GUIDE.md)
- Все `AUTO_BI_*`: [ENV_REFERENCE.md](ENV_REFERENCE.md)
- Прод, reverse-proxy, образ приложения: [DEPLOYMENT.md](DEPLOYMENT.md)
- Подключение своего DWH: [ONBOARDING_DWH.md](ONBOARDING_DWH.md)
