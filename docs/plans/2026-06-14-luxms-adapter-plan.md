# Luxms BI — адаптер: дизайн и план (GO-with-stand)

Дата: 2026-06-14. Часть остатка Phase 4 (продуктовые опции: Visiology/Luxms-адаптеры).
Источник — веб-research по `sandbox.demo.luxmsbi.com/docs` (live dev-guide). Парная оценка
Visiology — `2026-06-14-visiology-spike.md` (там вердикт NO-GO).

## TL;DR — GO, но live-верификация gated на креды/стенд

Luxms BI — **«метаданные в PostgreSQL, открытые как REST/CRUD»**: почти каждый объект (источники,
кубы, атласы, дашборды, дашлеты) адресуем через единый DB-API. Это **тот же
connection → SQL-dataset → charts → dashboard** контракт, что у Superset/DataLens, и чисто ложится
на `BIAdapter` Protocol. **Auth headless-дружелюбен** (session-cookie или JWT Bearer). Нативный
**ClickHouse** + SQL-subselect-кубы — точное совпадение с нашим DWH.

**Что блокирует автономную реализацию ПРЯМО СЕЙЧАС:** рабочие креды / запускаемый инстанс.
Публичный API `https://sandbox.demo.luxmsbi.com` живой и отвечает по документированному контракту
(проверено: `POST /api/auth/login` → `403 WRONG_PASSWORD_OR_USER_NAME`, `GET /api/v3/koob/...` →
`403 INVALID_SESSION`), но **валидных кредов нет**, а получить их = vendor demo-request / Docker-образ
по запросу (не self-serve; внешний контакт = гейт). Контракты реверсируемы по докам уже сейчас, но
**верификация payload'ов требует стенда** — а урок DataLens (3.2) показал, что доки расходятся с
реальностью (snake_case-поля, encoded id, транспорт через gateway). Поэтому payload-билдеры пишем
ТОЛЬКО против живого инстанса, не вслепую.

## Карта IR → контракты Luxms (по докам, требует live-подтверждения)

| `BIAdapter` метод | Luxms-контракт (документированный) | Открытые вопросы (на стенд) |
|---|---|---|
| `healthcheck` | `POST /api/auth/login` → сессия; затем дешёвый GET (напр. список атласов) | какой самый дешёвый авторизованный GET |
| `ensure_database` (source) | source = first-class объект (`source_ident`, id, name, scope global/atlas); создаётся через DB-API; нативный CH-коннектор | точный path/тело create source; reuse-by-name |
| `ensure_dataset` (cube) | `POST /api/db/koob.cubes/` `{name,title,source_ident,sql_query,config}`; PUT/DELETE для правки; batch=JSON-массив | схема `config`; как маппить IR-типы; идемпотентность |
| `create_chart` (dashlet) | dashlet = строка в `/api/db/${atlas}.dashlets`; конфиг по `dashlet-conf-guide` | точная схема dashlet-config per-viz |
| `assemble_dashboard` (atlas+dash) | дашборд = `/api/db/${atlas}.dashboards`; атлас — контейнер дашбордов и дашлетов | создание атласа; layout/binding дашлетов |
| `build` | оркестрация source→cube→dashlets→dashboard | порядок, какие id куда референсятся |
| данные (для контракт-теста) | `POST /api/v3/koob/data` / `/api/v3/{atlas}/data` (columns/filters/having/sort) | формат ответа для `_rendered_with_data` |

**Auth:** `POST /api/auth/login` (form-encoded) → cookie `LuxmsBI-User-Session` (24h) ИЛИ JWT
`Authorization: Bearer <jwt>` (для Super/Admin). Адаптер — как Superset/DataLens client: логин →
cookie-jar/заголовок.

**Viz-маппинг IR `Viz` → dashlet-тип** (по `dashlet-conf-guide/analytic`):
big_number→**Значение**, line→**Линии/Сплайн**, bar→**Столбцы**, stacked_bar→**Штабели**,
area→**Области**, pie→**Пирог/Пончик**, table+pivot→**Табличные**, heatmap→**Сетчатая (grid/matrix)**
(точный dashlet подтвердить на стенде). Гэпов для нашего набора нет.

## План реализации (когда будет стенд/креды)

Зеркало DataLens-трека (3.1 спайк → 3.2 реверс → адаптер → live contract-тесты):
1. **Спайк-реверс на живом инстансе**: login-path, create source (CH), `koob.cubes` create (SQL
   subselect), dashlet create per-viz, dashboard/atlas create, `/api/v3/koob/data` для рендера.
   Снять точные тела (как `2026-06-13-phase3.2-datalens-adapter-reversal.md`).
2. **Код** `auto_bi/adapters/luxms/`: `client.py` (auth + DB-API CRUD + koob), `dataset.py`
   (IR→cube payload + тип-маппинг), `chart_config.py` (IR viz→dashlet config), `adapter.py`
   (`LuxmsAdapter`, 5 методов Protocol + `build`). `TargetBI.LUXMS` в `ir/spec.py`; ветка в
   `adapters/factory.py`; `AUTO_BI_LUXMS_*` в `config.py`; UI-селектор +опция.
3. **Идемпотентность**: reuse/delete-then-create by name (как DataLens) — модель/path уточнить на
   стенде (DB-API CRUD это позволяет: GET по фильтру name → PUT/DELETE).
4. **Тесты**: unit shape + транспорт (httpx MockTransport, как `test_datalens_*`); live
   contract-сьют `tests/test_luxms_contract.py` (integration-gated, зеркало
   `test_datalens_contract.py`) — рендер реальных CH-данных через `/api/v3/koob/data`.

## Gate (что нужно от владельца, чтобы продолжить)

Одно из:
- **Демо-креды** для `sandbox.demo.luxmsbi.com` (email-level demo-request у вендора/партнёра), **или**
- **Self-hosted Docker-стенд** Luxms на Mac (образ — по demo-request; деплой как DataLens-стенд).

Без этого payload-билдеры писать нельзя (будут спекулятивны/неверны). При наличии — реализация
автономна (контракты документированы, shape-тесты на MockTransport не требуют стенда, live-сьют
гейтится как у DataLens). Оценка: сопоставима с DataLens-адаптером.
