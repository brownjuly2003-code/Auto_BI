# Task 2.7 — Enrichment UI (gaps → правка описаний/ролей → commit model.yaml)

## Goal
First-class enrichment workflow (ARCHITECTURE §3.2): gaps report в web UI, инлайн-правка описаний таблиц/колонок и ролей колонок, запись в model.yaml. Live-пробы grain'а остаются в CLI `auto_bi gaps` — API отдаёт offline-чеки.

## Tasks
- [x] 1. API: `create_app(..., model_path=None)`; `GET /api/v1/model/gaps` (offline find_gaps); `PATCH /api/v1/model/tables/{t}` {description}; `PATCH .../columns/{c}` {description?, role?, agg?} — валидация (agg только при role=measure, смена роли с measure сбрасывает agg), запись model.yaml под lock; 503 без model_path → Verify: test_api (5 новых тестов)
- [x] 2. serve/dev_ui_server: прокинуть model_path (dev — tmp-копия `.tmp/dev_model.yaml`) → Verify: dev-стенд сохраняет
- [x] 3. UI: секция «Качество модели» в правой панели — findings по severity, инлайн-редакторы описаний (таблица/колонки из detail) и select роли/agg (предзаполнены из /model/fields), save → re-fetch gaps → Verify: Playwright
- [x] 4. Docs: CLAUDE.md, ARCHITECTURE §3.7 → Verify: diff

## Done When
- [x] pytest зелёный (193 passed), ruff/black clean; Playwright-сценарий: правка описания date убрала её из finding (4→3→2 после revenue), `.tmp/dev_model.yaml` обновлён (description + agg: sum), консоль 0 ошибок
