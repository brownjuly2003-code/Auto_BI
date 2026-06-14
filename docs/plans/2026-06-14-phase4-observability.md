# Phase 4 — Observability (trace of agent steps + LLM-usage dashboard)

Дата: 2026-06-14. Ветка `phase-4/observability`. Трек выбран владельцем (Phase 4 «go»).

## Цель (PLAN.md Phase 4)

> Observability: трейс шагов агента на сессию, дашборд расходов LLM.

Две половины:
1. **Трейс шагов агента на сессию** — durable timeline шагов state-machine (grounding /
   clarify / propose / patch / advisor / approve) + фазы сборки, с таймингом и исходом.
2. **Дашборд расходов LLM** — агрегаты по `llm_calls`.

## Честность по данным (важно)

GraceKelly `/orchestrate` по проверенному контракту (gracekelly.py docstring, 2026-06-11)
**не возвращает usage/токены/стоимость** — только `status`/`output_text`/`failure_*`.
Поэтому «дашборд расходов LLM» строится на **реально измеримом**: число вызовов, латентность,
объём промпта (chars) и **объём ответа (completion_chars — НОВОЕ, берём из `len(output_text)`)**,
разбивка по модели / шагу агента / статусу / reasoning. Доллары/токены НЕ выдумываем; явная
пометка, что char-метрики — это size-прокси, а токен/$-учёт требует usage от оркестратора
(сегодня его нет).

## Дизайн

### 1. Store — schema v2 (миграция 1→2)
- `llm_calls` += `step TEXT NOT NULL DEFAULT ''` (какой шаг агента обслуживал вызов:
  `grounding`/`propose`/`patch`/`narrate`), `completion_chars INTEGER NOT NULL DEFAULT 0`.
- Новая таблица `trace_events(id, session_id, created_at, seq, kind, status, latency_ms, detail)`;
  `seq` — порядок внутри сессии. `kind ∈ {grounding, clarify, propose, patch, advisor, approve,
  build_start, build_done, build_error}`, `status ∈ {ok, error}`.
- Миграция: новые БД получают полную схему; существующие (user_version=1) — `ALTER TABLE
  llm_calls ADD COLUMN ...` + `CREATE TABLE trace_events` → `user_version=2`. Идемпотентно.
- Методы: `log_llm_call(..., step="", completion_chars=0)`; `add_trace_event(...)` (вычисляет seq);
  `trace_events(session_id)`; `llm_usage_summary()` (агрегаты для дашборда).

### 2. Инструментирование
- LLM client: захват `completion_chars` (= `len(output_text)`); проброс `step` через `complete()`
  (Protocol + GraceKellyClient + 3 call-site: grounding/propose/narrate + тест-дубли).
- Machine: тонкий `_trace(kind, status, latency_ms, detail)` + тайминг вокруг
  ground/propose/patch/advisor/approve.
- Build (api/app.py `_build`): `build_start` → `build_done`(detail=title, latency) /
  `build_error`(detail=error). Per-log-строки не трейсим (они и так стримятся SSE live).

### 3. Read API
- `GET /api/v1/sessions/{id}/trace` → `{session_id, events:[...], llm_calls:[...]}`.
- `GET /api/v1/observability/llm` → агрегаты: totals + breakdown по model/step/status/reasoning,
  латентность (total/avg/max), объёмы chars (prompt/completion), success-rate.

### 4. UI
- `<details>`-панель «Наблюдаемость» в spec-pane (как DCR/gaps): сводка LLM-usage + трейс
  активной сессии (шаги с таймингом). Спокойный белый стиль (preferences).
- Браузер-верификация: `scripts/dev_ui_server.py` + Playwright (stand-free).

### 5. Docs
- ARCHITECTURE.md — секция observability; PLAN.md — отметка Phase 4 observability;
  CLAUDE.md — статус. Финальный pytest/ruff/black; коммиты по вехам.

## Не в скоупе
- Реальный токен/$-учёт (нужен usage от GraceKelly — его нет).
- Внешний дашборд/Grafana/OTel-экспорт (in-app панели достаточно для single-user §2.1).
- Трейс каждой SSE-строки сборки (шум; durable summary = start/done/error).
