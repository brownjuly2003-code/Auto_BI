# E2 — Token-учёт по Anthropic-пути (observability)

> Roadmap `docs/plans/2026-06-29-roadmap-maximal.md` трек E, ID **E2** (🟢 АВТО).
> Закрывает задокументированный отложенный gap ARCHITECTURE §3.9 «токен/$-учёт отложен:
> требует, чтобы оркестратор отдавал usage». Anthropic Messages API **отдаёт**
> `usage.input_tokens/output_tokens` → захватываем их на этом провайдере.

## Проблема

`llm_calls` несёт только `completion_chars` (size-прокси — «честный размер ответа, потому что
GraceKelly не отдаёт токены»). Но `AnthropicClient` (второй провайдер, снимающий GraceKelly-SPOF)
получает реальный `response.usage` и **выбрасывает его**. Observability-панель показывает символы
там, где у Anthropic-провайдера есть настоящие токены.

## Дизайн (аддитивный, без смены модели данных — ровно как обещано в §3.9)

1. **Store v4 → v5.** Две **nullable** INTEGER-колонки `llm_calls.input_tokens` / `output_tokens`.
   - NULL = провайдер не сообщил usage (GraceKelly; транспортная ошибка до ответа). Это честный
     раздел: `completion_chars` остаётся универсальным прокси у всех вызовов; токены живут только
     там, где провайдер их вернул. NOT NULL DEFAULT 0 спутал бы «нет данных» с «ноль токенов».
   - Миграция `if version < 5:` — guarded `_add_column` (no-op на свежей БД), как v2/v4.
2. **Захват usage в `AnthropicClient._call`.** `usage = getattr(response, "usage", None)` сразу
   после ответа (до refusal-проверки → даже отказ учитывает потраченные входные токены).
   `input_tokens`/`output_tokens` инициализируются None (как `completion_chars=0`) — на транспортной
   ошибке остаются None.
3. **Шов `append_llm_log` + `store.log_llm_call`** получают опциональные `input_tokens`/`output_tokens`
   (default None). GraceKelly их не передаёт → None → прокси без изменений. jsonl-запись несёт их тоже.
4. **`llm_usage_summary`.** totals += `input_tokens`/`output_tokens` (`COALESCE(SUM,0)` — NULL
   игнорируется) + **`token_calls`** = число вызовов с не-NULL токенами (чтобы UI знал, есть ли
   реальные токены). `_breakdown` (by_model/by_step/by_status) += те же суммы.
5. **Панель «Наблюдаемость» (`app.js renderUsage`).** Токен-ячейки «вход/выход, токенов» — **только
   когда `token_calls > 0`**; символы остаются всегда (универсальный сигнал; смешанный провайдерами
   стор не превращаем в чисто-токенный вид). Доп. ячейки через тот же `statCell`.
6. **Доки.** ARCHITECTURE §3.9 «отложено» → «реализовано на Anthropic-пути; GraceKelly остаётся
   прокси»; module-docstring store (v5).

## $-стоимость = осознанный non-goal

Заголовок роадмапа упоминает «$», но тело E2 и gap §3.9 — про **токены** («аддитивные колонки»).
$ требует поддерживаемой таблицы цен (model → $/Mtok), которая дрейфует и провайдер-специфична —
против принципа универсальности и «не выдумывать работу». Токены — дрейф-устойчивая правда. $ —
тривиальная надстройка поверх токенов, когда появится владелец цен. Помечено в доке.

## Верификация

- Offline: pytest (store: версия 5 + миграция v4→v5 + token-summary + empty-summary; anthropic:
  захват usage из fake-response с `usage`; api: token-поля в агрегатах). ruff/black/mypy.
- UI: dev_ui_server (DevLLM эмитит токены) + Playwright → панель показывает «вход/выход, токенов»,
  консоль 0. Стенд CH **не нужен** (панель читает Store).
- Инварианты 1–8 целы (observability — read-only, не трогает IR/SQL/адаптеры).

## Точки входа

`store/db.py` (`_SCHEMA`/`_SCHEMA_VERSION`/`_migrate`/`log_llm_call`/`llm_usage_summary`),
`llm/_structured.py` (`append_llm_log`), `llm/anthropic.py` (`_call`/`_extract_usage`),
`api/static/app.js` (`renderUsage`), `scripts/dev_ui_server.py` (`DevLLM`),
`docs/ARCHITECTURE.md` §3.9. Тесты: `test_store.py`, `test_anthropic.py`, `test_api.py`.
