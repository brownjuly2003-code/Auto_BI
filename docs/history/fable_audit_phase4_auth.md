# S6-аудит Phase 4 — auth/RBAC (ветка `phase-4/auth-rbac`)

Дата: 2026-06-14. Ревьюер: субагент `code-reviewer` (Opus). Диф: `main..phase-4/auth-rbac`,
2 коммита (`87b1572` backend, `11cc7dd` UI). Security-критичный opt-in слой: pbkdf2-пароли,
revocable-токены, RBAC по DWH-схемам, cookie-сессия для UI. Метод: чтение полного дифа и
файлов, адверсариальный поиск обходов; pytest/ruff не гонял (оркестратор: 328 passed, clean).

## Вердикт

**Мержить можно — с оговоркой по P2.** Базовая модель надёжна: пароли pbkdf2_sha256 (240k
итераций, соль, `hmac.compare_digest`), токены `secrets.token_urlsafe(32)`, TTL-проверка в SQL
корректна, инъекций в токен-SQL нет (int-каст), cookie HttpOnly+SameSite=Lax, неаутентифициро-
ванного доступа к данным нет, logout инвалидирует токен в Store, миграция v2→v3 идемпотентна,
а инвариант «auth-off = без изменений» **соблюдён строго** (см. отдельный раздел).

P1 (блокеров безопасности) **нет**. Найдены **2×P2** (RBAC-обход на write-стороне модели +
утечка метаданных чужой сессии — оба требуют валидного токена ограниченного пользователя; в
текущей демо-модели один schema `dm`, поэтому тесты их не ловят) и **7×P3**. Рекомендация:
закрыть оба P2 до merge либо сознательно зафиксировать как known-limitation вслед за уже
задокументированным «сессии не привязаны к владельцу» (USER_GUIDE §7).

---

## P2 (закрыть до merge)

### P2-1. Enrichment-PATCH (`/model/tables/...`) НЕ под RBAC — write-side обход по чужим схемам
`auto_bi/api/app.py:254-308` (`update_table`, `update_column`).

**Суть.** Все остальные точки с моделью (`/model/fields`, `/model/gaps`, session start,
approve) скоупятся `filter_model_by_schemas`/`forbidden_tables` по `_user(request)`. А две
enrichment-ручки **не принимают `request` и не зовут `_user()`** вообще: гейтятся только
наличием токена (middleware), но не схемами.

**Путь эксплуатации.** Пользователь `bob` со `schemas: ["finance"]` (видит в `/model/fields`
пусто, не может строить над `dm`) логинится и шлёт:
```
PATCH /api/v1/model/tables/dm.sales/columns/revenue
  {"role": "dimension", "agg": null, "description": "..."}
```
Запрос проходит (токен валиден), `model.table("dm.sales")` находит таблицу вне его схем,
роль `revenue` меняется measure→dimension, `model.dump(path)` пишет на диск. Эффект глобальный:
изменённая роль/агрегация/описание влияет на **grounding и валидацию спеков ВСЕХ пользователей**
(модель — общий мутируемый объект, агент-сессии читают её же). Низкопривилегированный аналитик
ломает/подменяет семантику витрин, к которым у него нет доступа на чтение.

Почему тесты не поймали: `demo_model` содержит только `dm.*`, а `bob`(`["finance"]`) на любой
PATCH получит 404 (таблицы нет) — гэп маскируется одно-схемной фикстурой.

**Фикс.** Добавить `request: Request` в обе ручки и перед мутацией проверять схему:
```python
def update_table(table_name: str, body: TableUpdate, request: Request) -> dict:
    if not is_table_allowed(table_name, _user(request).allowed_schemas):
        raise HTTPException(status_code=403, detail="table outside your schemas")
    ...
```
(`is_table_allowed` уже есть в `auto_bi.auth`; `["*"]`→всегда True, поэтому auth-off не
затрагивается.) Плюс тест с двусхемной моделью (`bob` 403 на `dm.*`).

### P2-2. `GET /sessions/{id}/trace` (и `/{id}`, `/reply`, `/events`) не привязаны к владельцу — утечка/мутация чужой сессии
`auto_bi/api/app.py:485-495` (trace), `:445-453` (state), `:338-351` (reply), `:503-514` (events).

**Суть.** USER_GUIDE §7 честно помечает «сессии не привязаны к владельцу — следующий шаг», но
последствие шире, чем «адресация»: это сквозной доступ ограниченного пользователя к артефактам
чужой сессии. `_get(session_id)` резолвит сессию по id без сверки с `_user(request)`.

**Путь эксплуатации.** `admin` запустил сессию над `dm.*` (sid). `bob`(`["finance"]`) с валидным
токеном:
- `GET /api/v1/sessions/{sid}/trace` → отдаёт `trace_events` (detail = заголовок спека «…» +
  счётчики чартов), `llm_calls` (модель, prompt_chars/completion_chars, sha — не сырой промпт),
  `llm_usage`. Это метаданные о витринах вне его схем (что строит admin, по каким таблицам).
- `POST /api/v1/sessions/{sid}/reply {"text": "..."}` → **патчит чужой in-progress спек** через
  LLM (в пределах модели сессии — обычно широкой admin-модели), меняя предложение под admin'ом.
- `GET /api/v1/sessions/{sid}/events`, `GET /api/v1/sessions/{sid}` → статус/лог сборки.

Жёсткая граница на **данные DWH** держится: `approve` re-чекает `forbidden_tables` по КАЛЛЕРУ,
поэтому `bob` не соберёт дашборд над `dm` (403) и строк данных не увидит. Утечка — на уровне
**метаданных модели/намерений** + порча чужого workflow. session_id = `uuid4().hex` (128 бит,
не угадывается), что снижает практичность, но любой leak id (лог, история, реферер) открывает
доступ. Также `/observability/llm` (`:497`) отдаёт **глобальные** агрегаты по всем сессиям всех
пользователей — то же по сути.

**Фикс (минимум для MVP).** Привязать сессию к владельцу: при `manager.start` сохранять
`managed.owner = _user(request).username`, в `_get`/каждой session-ручке сверять
`managed.owner == _user(request).username` (admin — обход), иначе 404 (не 403, чтобы не
подтверждать существование). `/observability/llm` — admin-only либо скоуп по владельцу.
Если фикс откладывается — расширить оговорку §7: «ограниченный пользователь, знающий id чужой
сессии, видит её trace/usage и может слать в неё reply; данные DWH при этом не утекают (approve-
гейт)», и пометить P2 как принятый known-limitation в CLAUDE.md.

---

## P3 (бэклог / гигиена)

- **P3-1. `purge_expired_tokens` определён и протестирован, но НЕ вызывается** (`store/db.py:419`,
  нигде в `serve`). Истёкшие токены копятся в `auth_tokens` бесконечно. Не дыра (`token_user`
  отсекает по `expires_at > now`), но рост таблицы. Фикс: вызвать на старте `_serve` после
  `seed_users` (или периодически).
- **P3-2. Cookie без `Secure`** (`app.py:175-181`). При деплое за HTTPS (не localhost) токен
  поедет и по http. Фикс: `secure=True` управлять флагом конфига (для localhost-dev — off).
  SameSite=Lax+Origin-guard от CSRF достаточно для мутаций; `Secure` — про перехват на проводе.
- **P3-3. Тайминг-различие в login** (`app.py:168-171`): при неизвестном username
  `verify_password` не вызывается (нет хэша) → ответ быстрее, чем при верном username+неверном
  пароле. Username-oracle по времени. Минор (один opaque 401 по тексту уже есть). Фикс: гонять
  `verify_password` против dummy-хэша и при `row is None`.
- **P3-4. `seed_users` ре-хэширует пароли на каждом `serve`-старте** (`auth.py:182`) — новая соль
  каждый раз. Идемпотентно по username, но лишняя работа и инвалидация не происходит (токены
  живут). Косметика; для users-файла приемлемо.
- **P3-5. `_resolve_user` при `store is None` + auth on** (`app.py:100-103`): `row=None`→401 на
  всё. Поведение безопасное (fail-closed), но непрозрачное — оператор включил auth без store и
  получает 401 везде без диагностики. Фикс: в `create_app`/`_serve` падать рано с понятной
  ошибкой, если `auth_enabled and store is None`.
- **P3-6. UI user-chip через `textContent`** (`app.js:816`) — XSS нет (хорошо). Но username/role
  приходят с сервера из БД; при будущем self-service-регистре стоит держать `textContent`
  (зафиксировать инвариант). Логин-форма (`index.html`) и токен — токен НЕ в localStorage/URL
  (только HttpOnly-cookie + in-memory ответ login), в лог не пишется. Чисто.
- **P3-7. Демо-дефолты в конфиге** (`config.py:37-38`): `datalens_user/password = "admin"/"admin"`
  — не из этого дифа, но рядом с auth-флагами. `admin_password` дефолт пустой (хорошо: без него
  bootstrap-admin не создаётся, `seed_users` вернёт 0). Отметить, что auth без users-файла и без
  `AUTO_BI_ADMIN_PASSWORD` = ноль пользователей = всё 401 (намеренно, но стоит лог-предупреждения).

---

## Инвариант «auth-off = без изменений» (критично — проверено)

**Соблюдён строго.** Путь anonymous-admin эквивалентен прежнему single-user:

- Middleware (`app.py:124`) **всегда** ставит `request.state.user = ANONYMOUS_ADMIN` ДО любой
  ветки; при `auth_enabled=False` блок резолва токена не исполняется вовсе.
- `ANONYMOUS_ADMIN = AuthUser(role="admin", allowed_schemas=["*"])`.
- `filter_model_by_schemas(model, ["*"])` → `return model` (тот же объект, `is`-тест в
  `test_filter_model_wildcard_returns_same_object`), значит grounding/fields/gaps видят полную
  модель без копий.
- `forbidden_tables(spec, ["*"])` → `[]`: approve-гейт никогда не срабатывает.
- `/auth/login` при auth-off → 404 (не висит), `/me` отдаёт anonymous-admin.
- `create_app(auth_enabled=False)` — дефолт; `SessionManager.start` сигнатура не сломана
  (`model=None`→app-модель). CSRF-Origin-guard (F5) существовал до этой ветки — не регрессия.
- `test_api.py` обновлён только на `health` payload (`{"ok":True,"auth":False}`); остальной
  auth-off контракт покрыт прежним сьютом (оркестратор: 328 passed).

Регрессий для выключенного auth не выявлено.

---

## Что сделано хорошо

- Пароли: pbkdf2_sha256, 240k итераций (адекватно 2026), 16-байт соль, self-describing формат,
  `hmac.compare_digest`. `verify_password` на битом хэше не падает (`except (ValueError, TypeError)`
  → False; покрыто `test_verify_rejects_malformed_hash`).
- Токены: 32 байта энтропии urlsafe; `create_token` интерполирует `int(ttl_hours)` (инъекции нет);
  TTL-проверка `expires_at > datetime('now')` корректна; logout реально `DELETE`-ит токен в Store.
- Cookie: HttpOnly (JS не читает), SameSite=Lax + Origin-guard — разумная CSRF-защита для MVP.
- Login: один opaque 401 для неверного username и пароля (не палит, какой именно).
- Middleware: ни один мутирующий/данные-эндпоинт не открыт случайно — `_open_paths` = только
  health+login, всё `/api/v1/*` под токеном; `/` и `/static` (UI-загрузка) вне `/api/v1/`.
- RBAC defense-in-depth: grounding скоупится (модель-копия), approve re-чекает forbidden_tables
  по спеку ДО перехода машины (denied approve без side-effect). Patch через `reply` валидируется
  против модели сессии — выдуманную чужую таблицу не вкрутить.
- Миграция v2→v3 идемпотентна (только новые `CREATE TABLE IF NOT EXISTS` + bump user_version),
  legacy v0/v1/v2 БД не роняются (тесты на месте).

---

## Резолюция (закрыто перед merge, 2026-06-14)

Оба P2 закрыты в той же ветке `phase-4/auth-rbac`:

- **P2-1 — write-side RBAC на enrichment-PATCH.** `update_table`/`update_column` теперь принимают
  `request` и зовут `_require_table_access(table_name, request)` (403, если схема таблицы вне
  `allowed_schemas`; `["*"]`→разрешено, auth-off не затронут). Проверка ПЕРЕД `_model_path()`
  (403 раньше 503). Тест `test_enrichment_patch_requires_schema_access`.
- **P2-2 — owner-binding сессий.** `ManagedSession.owner` (+ `SessionManager.start(owner=…)`), хелпер
  `_owned(session_id, request)`: при auth-on не-владелец и не-admin → **404** (existence скрыт).
  Применён к `reply`/`approve`/`session_state`/`delete`/`events` (`_owned`==`_get` при auth-off,
  без регресса) и к `trace` (гейт только при auth-on; auth-off сохраняет прямое чтение из Store,
  переживающее eviction). `owner` = username при auth-on, иначе None. Тест `test_session_owner_isolation`.

P3 (purge-вызов, cookie Secure для HTTPS, login-тайминг по username, store-None при auth-on,
seed ре-хэш, observability/llm = глобальные агрегаты) — бэклог, не блокеры. pytest 329, ruff/black clean.
