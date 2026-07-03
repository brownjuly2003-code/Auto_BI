# Phase 2.1 — FastAPI backend поверх agent core (2026-06-12, Fable)

## Goal
HTTP-контур для web UI: сессии диалога (start/reply/approve), SSE-стрим шагов сборки. Ядро (AgentSession/pipeline) не меняется — только обвязка. Стенд недоступен → всё на фейках, live-смоук позже.

## Tasks
- [x] deps: `uv add fastapi uvicorn` → Verify: import fastapi в venv
- [x] `auto_bi/api/schemas.py`: TurnResponse (phase/message/questions/spec/verdicts/error), SessionState, BuildEvent → Verify: mypy-ничего, просто pydantic
- [x] `auto_bi/api/sessions.py`: SessionManager — реестр AgentSession + per-session lock + буфер событий сборки (replay для SSE) → Verify: unit на двойной approve / unknown id
- [x] `auto_bi/api/app.py`: `create_app(model, llm, advisor, store, builder)` (DI для тестов) + продакшн-wiring из settings; endpoints: POST /api/v1/sessions, POST .../reply, POST .../approve (202, build в фоне), GET .../events (SSE), GET /api/v1/sessions/{id}, GET /api/v1/health → Verify: tests/test_api.py
- [x] Ошибки: неудачная правка (SpecValidationError/LLMError) НЕ теряет сессию — 200 + error, phase=approve (зеркало F6); 404 unknown, 409 wrong phase → Verify: тесты
- [x] CLI: `auto_bi serve [--host --port --model-path]` (uvicorn, lazy imports) → Verify: ручной smoke `--help`
- [x] Финал: pytest+ruff+black, CLAUDE.md/ARCHITECTURE.md статус, коммиты

## Done When
- pytest зелёный (все новые API-тесты на ScriptedLLM + fake builder), ruff/black clean.
- Полный диалоговый цикл проходит через HTTP: start → clarify → approve → SSE события → built url; store фиксирует messages/specs/builds.

## Notes
- LLM-вызовы синхронные (GraceKelly ≤300s) — sync-endpoints FastAPI идут в threadpool, ок для v1.
- Сборка в threading.Thread, события в queue + буфер; SSE = StreamingResponse, без sse-starlette.
- Инварианты §1–8 не трогаем; advisor advisory-only остаётся в machine.
