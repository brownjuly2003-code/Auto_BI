# Phase 4 — остаток: auth/RBAC + Visiology/Luxms адаптеры

Дата: 2026-06-14. По «бери в работу остаток, делай всё сам; Visiology/Luxms — тоже нужно».
Ветки: `phase-4/auth-rbac`, далее `phase-4/visiology-luxms`.

Остаток PLAN.md Phase 4: (1) Auth/мульти-юзер + RBAC по DWH-схемам; (2) продуктовые опции по
спросу — **Visiology / Luxms адаптеры** (явно затребованы), реестровая упаковка, новые движки.

---

## Часть 1. Auth/мульти-юзер + RBAC по DWH-схемам

Сейчас API/UI без аутентификации (`app.py`: «unauthenticated by design — §2.1»). Делаем
**opt-in** аутентификацию (дефолт OFF), чтобы CLI, тесты и single-user-режим работали как
раньше; при `AUTO_BI_AUTH_ENABLED=true` API требует токен и применяет RBAC.

### Дизайн (без новых зависимостей — stdlib)
- **Хэш паролей**: `hashlib.pbkdf2_hmac("sha256", pw, salt, iterations)`; формат
  `pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>`; сверка через `hmac.compare_digest`.
- **Токены**: `secrets.token_urlsafe(32)`, хранятся в Store (revocable), TTL из конфига.
- **Пользователи**: из YAML-файла (`AUTO_BI_AUTH_USERS_FILE`) — `username/password/role/schemas`;
  при пустом файле и включённом auth — бутстрап одного admin из `AUTO_BI_ADMIN_*`. Пароли в
  файле плейнтекстом (операторский секрет как `.env`, файл в `.gitignore`), хэшируются при seed.
- **RBAC**: у пользователя `allowed_schemas` (префикс до первой `.` в имени таблицы `dm.sales`),
  `["*"]` = все. Точки применения:
  1. session start → агенту передаётся **отфильтрованная по схемам копия модели** (grounding
     видит только разрешённое; защита в глубину) — `filter_model_by_schemas` (model_copy + drop
     таблиц/джойнов вне allowed).
  2. `GET /model/fields`, `/model/gaps` → фильтр по allowed (UI не показывает чужое).
  3. `approve`/build → каждая таблица spec'а в allowed-схемах, иначе **403** (жёсткая граница).

### Store (миграция v2→v3, идемпотентно)
- `users(id, username UNIQUE, password_hash, role, allowed_schemas TEXT)` (schemas как JSON-массив).
- `auth_tokens(token PK, user_id, created_at, expires_at)`.
- Методы: upsert_user / get_user / list_users / create_token / token_user (с TTL-проверкой) /
  delete_token / purge_expired_tokens.

### API
- `POST /api/v1/auth/login {username,password}` → `{token, expires_at, user:{username,role,schemas}}`.
- `POST /api/v1/auth/logout` (Bearer) → 204, токен удалён.
- `GET /api/v1/auth/me` (Bearer) → текущий пользователь.
- FastAPI-зависимость `current_user`: при auth OFF → синтетический admin (всё разрешено,
  поведение как сейчас); при ON → резолв Bearer-токена, иначе 401. Применяется к защищаемым
  роутам; `/health` и статика открыты.

### Этапы
- **A (backend)**: config-флаги, `auto_bi/auth.py` (hash/token/RBAC-фильтр + загрузка users-файла),
  Store v3, API login/logout/me + зависимость + RBAC в fields/gaps/start/approve. Тесты (auth
  off=как раньше; on=401/200, RBAC 403, фильтр модели/полей, TTL/logout). ruff/black, pytest.
- **B (UI)**: минимальный логин-экран при включённом auth (токен в localStorage, Authorization
  header в fetch/SSE), Playwright-верифа на dev-сервере. Доки USER_GUIDE §auth.

---

## Часть 2. Visiology / Luxms адаптеры

Адаптер = реализация `BIAdapter` Protocol (6 методов) + запись в `TargetBI` enum + ветка
`make_adapter` + UI-селектор + контракт-тесты. По опыту DataLens (3.1 спайк → 3.2 реверс на
живом стенде, несколько сессий) **верифицированный адаптер требует живого инстанса** — публичные
доки расходятся с реальностью (snake_case поля, encoded id, транспорт через gateway и т.п.).

**Feasibility (research-субагент, в работе)** определяет путь по каждой платформе:
- **GO (есть free self-host/demo + REST API + email-only)** → как DataLens: спайк-реверс →
  адаптер → live contract-тесты.
- **SPIKE/NO-GO (нужен платный license / sales / нет API)** → честный спайк-док (API-карта из
  публичных доков, IR→capability, что блокирует) + adapter seam: `TargetBI.VISIOLOGY/LUXMS`,
  ветка фабрики, скелет `adapters/<platform>/` (client + payload-билдеры по докам + shape-тесты),
  но без live-верификации — **gate** на получение стенда/кредов. Не шипим непроверенный build-путь
  в дефолт (опт-ин/NotImplemented для assemble до живого реверса).

### Часть 3. Реестровая упаковка / новые движки
Ниже приоритетом. Реестр = упаковка для дистрибуции (это про deploy/packaging, не код-логику) —
оформить как задачу/доку по необходимости. Новые движки — по конкретному запросу.

---

## Порядок и верификация
1. Auth/RBAC Stage A → commit → Stage B → commit → merge.
2. Visiology/Luxms по feasibility (отдельная ветка).
3. Каждый шаг: pytest зелёный, ruff/black, локальный коммит на вехах; merge в main локально
   (репо без remote → push невозможен). UI-части — Playwright на dev-сервере.
