# Onboarding нового DWH за ≤ 1 час

Как подключить к Auto_BI новую витрину данных (DM-слой DWH) и довести её до состояния, в
котором агент строит по ней дашборды. Цель — уложиться в час. Поддержанные движки:
**ClickHouse** (v1, путь через CLI) и **Greenplum/Greengage** (v2, путь через Python-API —
см. §6).

Принцип: интроспекция даёт **черновик** семантической модели, дальше его обогащают
(dbt-артефакты + ручные правки) и проверяют. Ручные правки в `model.yaml` всегда выигрывают
у автоматики.

---

## Бюджет времени (ориентир ≤ 1 ч)

| Шаг | Время |
|---|---|
| 0. Доступы (read-only роль, сеть) | 10–15 мин |
| 1. `.env` | 5 мин |
| 2. Интроспекция → черновик `model.yaml` | 2–5 мин |
| 3. Обогащение (dbt-import / ручные описания, joins) | 15–25 мин |
| 4. Gaps-отчёт → закрыть слепые зоны | 5–10 мин |
| 5. Проверка (тестовая сборка / advisor) | 5–10 мин |

---

## Шаг 0. Доступы

- **Read-only роль** в DWH на DM-схему (Auto_BI только читает: интроспекция системных
  каталогов + `EXPLAIN`/`LIMIT`-прогоны при валидации SQL). Писать в DWH агент не должен.
- **Сетевой доступ** с машины, где запускается Auto_BI, до DWH (и до BI-сервера, и до
  GraceKelly). Если CH доступен через SSH-туннель, а BI видит его под другим адресом —
  понадобятся `AUTO_BI_CH_HOST_FROM_BI` / `_PORT_FROM_BI` (§1).

## Шаг 1. `.env`

В корне репозитория, префикс `AUTO_BI_` (полный список — [USER_GUIDE.md](USER_GUIDE.md) §6).
Минимум для ClickHouse:

```dotenv
AUTO_BI_CH_HOST=localhost
AUTO_BI_CH_PORT=8123
AUTO_BI_CH_USER=auto_bi_ro
AUTO_BI_CH_PASSWORD=...
AUTO_BI_CH_DATABASE=dm
```

Для Greenplum/Greengage — блок `AUTO_BI_GP_*`:

```dotenv
AUTO_BI_GP_HOST=localhost
AUTO_BI_GP_PORT=5432
AUTO_BI_GP_USER=auto_bi_ro
AUTO_BI_GP_PASSWORD=...
AUTO_BI_GP_DATABASE=postgres
AUTO_BI_GP_SCHEMA=dm
```

## Шаг 2. Интроспекция → черновик модели (ClickHouse)

```bash
auto_bi introspect --output semantic/model.yaml
# при необходимости другая БД:
auto_bi introspect --database my_dm --output semantic/model.yaml
```

Интроспектор читает таблицы, колонки, типы и физику движка (для CH — ключи сортировки/
партиционирования, оценки размеров) и пишет черновик `model.yaml`. Команда печатает
`Draft written to ...: N tables, M columns`.

Для Greenplum интроспекция сейчас выполняется через Python-API (CLI `introspect` пока
только ClickHouse) — см. **§6**.

## Шаг 3. Обогащение модели

Черновик содержит структуру, но не бизнес-смысл. Обогатите:

**3а. Из dbt-артефактов** (если витрины описаны в dbt) — переносит описания таблиц/колонок
и связи (relationships → joins, fk):

```bash
auto_bi dbt-import --manifest target/manifest.json --catalog target/catalog.json --dry-run
auto_bi dbt-import --manifest target/manifest.json --catalog target/catalog.json
```
Заполняет **только пустые** значения — ручные правки не перетираются. `--dry-run` показывает,
что изменится, без записи. Сопоставление — по `identifier` таблицы.

**3б. Вручную** — отредактируйте `semantic/model.yaml`: понятные описания таблиц/колонок,
синонимы, роли мер/измерений, и **joins** между таблицами (явные пары `on_left`/`on_right`,
которые должны быть рёбрами модели — иначе агент не сможет тянуть поля из смежных таблиц;
семантика join'ов — ARCHITECTURE §3.4). Чем понятнее модель, тем меньше уточнений у агента.

## Шаг 4. Gaps-отчёт — закрыть слепые зоны

```bash
auto_bi gaps --offline                 # без подключения к DWH
auto_bi gaps --output docs/gaps.md     # с живым профилированием грейнов времени
```

Отчёт перечисляет, где модель «слепая»: таблицы/колонки без описаний, неоднозначные роли,
непокрытые временные грейны. Закройте критичное правками в `model.yaml` и при желании
прогоните gaps ещё раз.

## Шаг 5. Проверка

```bash
# детерминированный advisor — офлайн, без LLM/стенда:
auto_bi eval --suite advisor

# живая сборка тестового дашборда (нужен GraceKelly + BI):
auto_bi build "<простой запрос по новой витрине>"
```

Если `eval --suite advisor` зелёный, а `build` вернул ссылку на дашборд с реальными
данными — витрина подключена. Дальше — `auto_bi serve` для работы через web UI.

---

## 6. Greenplum / Greengage: интроспекция через Python-API

CLI-команда `introspect` пока поддерживает только ClickHouse. Для Greenplum используйте
интроспектор напрямую (после заполнения `AUTO_BI_GP_*` в `.env`):

```python
from auto_bi.config import get_settings
from auto_bi.introspect.greenplum import GreenplumIntrospector, make_run_query_pg

settings = get_settings()
model = GreenplumIntrospector(
    make_run_query_pg(settings), schema=settings.gp_schema
).introspect()
model.dump("semantic/model_gp.yaml")
```

Интроспектор читает PG-каталоги + распределение/партиционирование Greenplum
(`pg_get_table_distributedby`, многоуровневые `pg_partition`, `pg_stats.n_distinct`), оценивает
строки по партиц-детям. Дальше — те же шаги 3–5, но `--model-path semantic/model_gp.yaml`
(eval сам выберет GP-набор правил по `physical.engine`).

> **Ограничения GP:** Auto_BI использует Greenplum для **интроспекции и advisor**; сборка
> дашборда в BI идёт всегда через ClickHouse-коннекшн (GP-BI-сборка не реализована). Полноценный
> CLI-путь `introspect --engine greenplum` — возможное будущее улучшение (интроспектор уже есть).

---

См. также: [USER_GUIDE.md](USER_GUIDE.md) (использование), [ARCHITECTURE.md](ARCHITECTURE.md)
§3.1–3.2 (интроспекция и семантическая модель), §3.4 (joins в IR).
