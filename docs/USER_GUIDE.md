# Auto_BI — руководство пользователя

Как пользоваться Auto_BI: превратить запрос (текстом или раскладкой полей витрин) в
дашборд в выбранной BI. Здесь — практика; архитектура и «почему так» — в
[ARCHITECTURE.md](ARCHITECTURE.md), подключение нового DWH — в [ONBOARDING_DWH.md](ONBOARDING_DWH.md).

---

## 1. Что нужно до начала

- **Python ≥ 3.12** и установленный пакет: `pip install -e .` в корне репозитория
  (даёт консольную команду `auto_bi`).
- **Семантическая модель** витрин — файл `semantic/model.yaml`. Если его нет, сначала
  сделайте интроспекцию DWH (см. [ONBOARDING_DWH.md](ONBOARDING_DWH.md) или быстрый старт ниже).
- **DWH** (ClickHouse — v1) с read-only ролью, доступный с машины, где запускается Auto_BI.
- **BI** — Apache Superset (v1) или self-hosted Yandex DataLens (v2).
- **LLM** — сервис GraceKelly на `http://127.0.0.1:8011` (Sonnet 4.6 thinking). Нужен для
  диалога/предложения spec'а; детерминированные шаги (валидация, advisor, сборка) от него не зависят.

Все секреты и адреса — через переменные окружения с префиксом `AUTO_BI_` или файл `.env`
в корне (см. §6). Секреты в коде/доках не хранятся.

---

## 2. Быстрый старт (ClickHouse + Superset)

```bash
# 1. установка
pip install -e .

# 2. настройка доступа (пример .env — значения свои)
#    AUTO_BI_CH_HOST=localhost   AUTO_BI_CH_USER=auto_bi_ro   AUTO_BI_CH_PASSWORD=...
#    AUTO_BI_CH_DATABASE=dm
#    AUTO_BI_SUPERSET_URL=http://localhost:8088   AUTO_BI_SUPERSET_PASSWORD=...
#    AUTO_BI_GRACEKELLY_URL=http://127.0.0.1:8011

# 3. интроспекция DWH -> черновик модели
auto_bi introspect --output semantic/model.yaml

# 4. (опц.) что в модели «слепо» и стоит уточнить
auto_bi gaps --offline

# 5а. одношаговая сборка из текста
auto_bi build "Выручка по магазинам за июнь 2026, топ-10"

# 5б. или диалог с уточнениями и подтверждением
auto_bi chat

# 5в. или web UI (текст + drag&drop полей)
auto_bi serve            # http://127.0.0.1:8200
```

Команда печатает ссылку на готовый дашборд.

---

## 3. Команды CLI

Запуск: `auto_bi <команда> [опции]`. Везде `--model-path` по умолчанию `semantic/model.yaml`.

| Команда | Назначение |
|---|---|
| `build "<запрос>"` | Happy-path: текст → spec → сборка дашборда, без диалога. `--target superset\|datalens` (по умолчанию `superset`). |
| `chat` | Диалог: уточнения → превью spec → `да` собрать / правка словами / `отмена`. После сборки можно дорабатывать словами (итерации). |
| `serve` | HTTP API + web UI (FastAPI/uvicorn). `--host` (def `127.0.0.1`), `--port` (def `8200`). |
| `introspect` | Интроспекция DWH → черновик `model.yaml` (ClickHouse). `--database`, `--output`. |
| `gaps` | Детерминированный отчёт «что в модели не заполнено / неоднозначно». `--offline` без подключения к DWH, `--output file.md` в файл. |
| `eval` | Прогон eval-сьютов. `--suite advisor\|golden\|all`, `--cases id1,id2` (подмножество). advisor — офлайн; golden — через живой GraceKelly. |
| `dbt-import` | Обогащение `model.yaml` из dbt-артефактов (описания, связи). `--manifest` (обяз.), `--catalog`, `--dry-run`. Заполняет ТОЛЬКО пустые значения — ручные правки всегда выигрывают. |

### build
```bash
auto_bi build "Динамика выручки по дням за Q2 2026"
auto_bi build "Топ-10 городов по выручке" --target datalens
```
Запрос проходит grounding по модели → PROPOSE_SPEC (LLM) → валидация spec и SQL
(sqlglot + EXPLAIN/LIMIT-прогон на живом DWH) → детерминированная сборка адаптером.
Если модель не найдена — подсказывает запустить `introspect`.

### chat
Интерактивный REPL. Слова-подтверждения: `да / ок / строй / собирай / build / yes / +`.
Выход: `выход / quit / exit / q`. После сборки дашборда правка словами доработает spec и
пересоберёт; неудачная правка не теряет сессию — дашборд остаётся прежним.

### eval
```bash
auto_bi eval --suite advisor                     # офлайн, без LLM/DWH
auto_bi eval --suite golden                       # через GraceKelly (живой)
auto_bi eval --suite all --cases g1,g12_revenue_by_city_join
```
Движок берётся из модели (`physical.engine`), сьюты выбираются под него (CH-набор vs GP-набор).
Прогонять перед любым изменением промптов.

---

## 4. Web UI (`auto_bi serve`)

Открывается на `http://<host>:<port>/` (по умолчанию `127.0.0.1:8200`). Без сборочной
цепочки — статика отдаётся FastAPI.

**Два режима ввода:**
- **Текстом** — опишите дашборд словами; агент задаёт уточнения только при реальных
  расхождениях с данными.
- **Полями** (fields-first) — drag&drop (или клик-фоллбек) полей витрин в черновики
  чартов; агент превращает раскладку в spec и показывает детерминированный анализ.

**Что на экране:**
- **Чат** — уточняющие вопросы, ошибки правок.
- **Превью spec** — карточки чартов + вердикты Feasibility Advisor (полоса акцента по
  severity) и предупреждения. Кнопка **«Собрать / Пересобрать»**, SSE-лог сборки, ссылка
  на дашборд.
- **Выбор BI** — Superset / DataLens (значение шлётся при старте, дальше фиксируется на сессию).
- **Итерации** — после сборки правка словами патчит spec и пересобирает.
- **Заявки владельцу DM** (dm change requests) — если запрос витриной не предусмотрен,
  advisor оформляет заявку; список и статусы видны в UI.
- **Панель «Наблюдаемость»** (сворачиваемая) — трейс шагов агента на сессию (с таймингом
  и исходом) + сводка расходов LLM (вызовы / латентность / объёмы в символах). Подробнее — §5.

---

## 5. Feasibility Advisor и наблюдаемость

**Advisor** — детерминированный (без LLM) контролёр: сверяет запрос с физикой витрины
(ключи сортировки/партиции, размеры, EXPLAIN-evidence) и выдаёт вердикты `info / warn /
critical`. Прямо говорит, когда дашборд витриной не предусмотрен (вплоть до «это запрос на
новую витрину»), и умеет оформить заявку владельцу DM. Правила движко-зависимы (ClickHouse
vs Greenplum/Greengage).

**Наблюдаемость** (Phase 4): для каждой сессии Auto_BI пишет durable-трейс шагов
(grounding / clarify / propose / patch / advisor / approve + фазы сборки) с таймингом и
исходом, и агрегаты по вызовам LLM. API: `GET /api/v1/sessions/{id}/trace` и
`GET /api/v1/observability/llm`.

> **Честность по данным:** GraceKelly не возвращает токены/стоимость, поэтому «расходы LLM»
> построены на измеримом — число вызовов, латентность и **объём в символах** (промпт +
> ответ). Символьные метрики — это **size-прокси, НЕ токены и НЕ доллары**. Точный
> токен/$-учёт появится, когда оркестратор начнёт отдавать usage. Подробнее — ARCHITECTURE §3.9.

---

## 6. Конфигурация (переменные окружения)

Префикс `AUTO_BI_`, читается из окружения или `.env` в корне. Ключевые:

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `AUTO_BI_CH_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DATABASE` | ClickHouse DWH (read-only роль) | `localhost` / `8123` / `auto_bi_ro` / `` / `dm` |
| `AUTO_BI_CH_HOST_FROM_BI` / `_PORT_FROM_BI` | CH адрес, как его видит сервер BI (если отличается от CLI-стороны, напр. через туннель) | `` / `0` |
| `AUTO_BI_GP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_DATABASE` / `_SCHEMA` | Greenplum/Greengage DWH (v2) | `localhost` / `5432` / `auto_bi_ro` / `` / `postgres` / `dm` |
| `AUTO_BI_SUPERSET_URL` / `_USER` / `_PASSWORD` | Apache Superset | `http://localhost:8088` / `admin` / `` |
| `AUTO_BI_DATALENS_URL` / `_USER` / `_PASSWORD` / `_WORKBOOK_ID` | self-hosted DataLens (v2) | `http://localhost:8090` / `admin` / `admin` / `ra7f79yirtumb` |
| `AUTO_BI_CH_HOST_FROM_DATALENS` | CH-хост, как его достаёт DataLens-коннекшн | `host.docker.internal` |
| `AUTO_BI_GRACEKELLY_URL` / `_MODEL` | LLM-сервис | `http://127.0.0.1:8011` / `claude-sonnet-4-6` |
| `AUTO_BI_SEND_SAMPLES` | слать ли примеры значений в grounding | `true` |
| `AUTO_BI_STORE_PATH` | SQLite-стор (сессии, spec'ы, сборки, llm_calls, заявки DM) | `data/auto_bi.sqlite` |

---

## 7. Частые ситуации

- **`Semantic model not found: ...`** — нет `model.yaml`. Запустите
  `auto_bi introspect --output semantic/model.yaml` (см. [ONBOARDING_DWH.md](ONBOARDING_DWH.md)).
- **Запрос не выражается в IR** (напр. поле из другой таблицы без join'а) — превью честно
  покажет реальные поля; агент не молчит. Решение — указать join в модели (см.
  ARCHITECTURE §3.4) или переформулировать.
- **Advisor выдал `critical`** — витрина не предусматривает такой паттерн (фуллскан большого
  факта, неколокированный join, фильтр не по партиции). Это не баг: либо примите вариант
  advisor, либо оформите заявку владельцу DM.
- **DataLens-сборка** — нужен живой self-hosted стенд; BI-коннекшн адаптера всегда смотрит в
  ClickHouse (GP-сборка в BI не поддержана — GP используется для интроспекции и advisor).

---

См. также: [ARCHITECTURE.md](ARCHITECTURE.md) (дизайн), [PLAN.md](PLAN.md) (фазы),
[ONBOARDING_DWH.md](ONBOARDING_DWH.md) (подключение нового DWH).
