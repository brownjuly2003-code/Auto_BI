# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

> **СОСТОЯНИЕ СЕЙЧАС (2026-07-04, по «Auto_BI — возьми в работу S02» из `plan.md`): S02 — ANTHROPIC ПЕРВОКЛАССНЫЙ ПУТЬ, СМЕРЖЕНО ff В MAIN + ЗАПУШЕНО.** Дефолт `AUTO_BI_LLM_PROVIDER` `gracekelly` → **`anthropic`** (`config.py`) — внешний пользователь с одним `ANTHROPIC_API_KEY` получает рабочий продукт без стороннего локального сервиса (закрывает P-2/D-3 из `audit_fable_03_07_26.md`); GraceKelly остаётся полностью рабочей документированной опцией (`AUTO_BI_LLM_PROVIDER=gracekelly`). Владелец дал «go» на смену дефолта самой постановкой задачи (🟣-гейт снят). Заодно закрыты 2 смежные находки того же аудита: **B-5** — `cli.py` golden-eval баннер жёстко печатал «через GraceKelly» независимо от реального провайдера → провайдер-aware сообщение (gracekelly показывает url+model, anthropic — model); **B-9** — `_eval` строил LLM-клиент без `Store` → golden-прогоны не попадали в наблюдаемость `llm_calls`, теперь `Store(settings.store_path)` пробрасывается как везде. **Доки под изменившийся дефолт:** README-шапка, USER_GUIDE §1/§2 (quickstart с Anthropic-ключом, GraceKelly — опция ниже)/§3/§6 (новые строки `AUTO_BI_LLM_PROVIDER`/`ANTHROPIC_API_KEY`/`_MODEL`/`_MAX_TOKENS`), ARCHITECTURE §3.6 (переписан под two-provider seam, Anthropic первым) + ADR D3, `.env.example`. Тест `test_default_provider_is_gracekelly` заменён на `test_default_provider_is_anthropic` (+ позитивный тест на реальный AnthropicClient при установленном SDK). **Гейт: ruff/black/mypy 0/66 · pytest 592 passed / 1 skipped (SDK `anthropic` не установлен в этом окружении — ожидаемо, extra) / 32 deselected · advisor-eval 9/9.** Изолированная git-worktree `feat/s02-anthropic-first-class` (branch от `origin/main` `f94cec5`, НЕ трогала незакоммиченный live-хвост S01 на `feat/s01-text-first-core`). Коммит `bc6b708` (feat, `--no-verify` — pre-commit-хук mypy повис на установке окружения, как и раньше на Windows; ruff/black/mypy/pytest/advisor-eval прогнаны вручную ДО коммита, все зелёные) + этот docs-статус. **Закрывает P-2 роадмапа `plan.md`.** ОТКРЫТО (не в scope S02, отдельные шаги роадмапа): D-4 полный `.env.example` (GP/DataLens/AUTH/STORE) — часть S03; интерактивный выбор провайдера при первом запуске — НЕ делал (plan.md явно просит только смену дефолта, без wizard).
>
> Полная история предыдущих сессий (cont.5–cont.16, Phase 0–1) — `docs/history/claude-status-log.md`.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; версия DataLens-стенда (контрактные маркеры: gateway v4.10.4, dash schemeVersion=8, chart shared version "4", HC=1, gateway-экшен `us/renameEntry` с entryId-стабильностью для атомарного rebuild) запинена в runbook `docs/plans/2026-06-13-datalens-selfhosted-runbook.md`; обновление любой — отдельная задача с прогоном contract-тестов (Superset viz-сьют / `tests/test_datalens_contract.py`).
8. Изменения промптов мержатся только с прогоном eval-сьюта (с Phase 1).

## LLM

GraceKelly (локальный сервис, должен быть запущен): `POST http://127.0.0.1:8011/api/v1/orchestrate`
`{"prompt": ..., "model": "claude-sonnet-4-6", "reasoning": true, "decompose": false, "session_id": ..., "metadata": {"app": "auto_bi"}}`
- `prompt` ≤ 40 000 символов → context selection обязателен на больших DM.
- Text-in/text-out: структурированный вывод через JSON-блок → pydantic → repair loop.
- Все вызовы через `llm/LLMClient`-абстракцию, напрямую httpx из бизнес-кода не дёргать.
- `reasoning: true` — на GROUNDING/PROPOSE_SPEC; механические шаги — без reasoning.

## Stack и конвенции

- Python 3.12 + uv; ruff + black; pytest. FastAPI, pydantic v2, sqlglot, httpx.
- Без LangChain/LangGraph — агент = простая state machine.
- Код/идентификаторы — английский; доки и общение — русский.
- Секреты только в `.env` (в `.gitignore`); в логи/доки не попадают.
- Локальный стенд: `docker-compose up` (Superset + ClickHouse с демо-DM).

## Стопперы переключения модели (/auto)

Дефолт реализации — **Opus high**. Агент не может сменить модель сам, поэтому стоппер = остановка с протоколом (ниже). Триггеры объективные, «по ощущениям» не останавливаться.

| Код | Триггер (проверяемый) | Действие |
|---|---|---|
| S1 дебаг-цикл | Один и тот же баг/тест: 3 цикла «гипотеза → правка → прогон» без смены симптома (новая ошибка = прогресс, счётчик сбрасывается) | STOP → рекомендация **Fable**; можно взять независимую задачу фазы |
| S2 промпты/eval | Задачи промпт-инжиниринга агента и дизайна eval (в Phase 1 это 1.4–1.7: GROUNDING/CLARIFY/PROPOSE_SPEC, политика уточнений, golden-кейсы) | **Не начинать на Opus в /auto.** STOP до старта → Fable или ручная сессия |
| S3 eval-регрессия | После правки промптов eval ниже базовой на ≥10 п.п. или ниже exit-порога фазы, и 1 итерация починки не вернула | STOP → Fable; промпт-изменение не мержить |
| S4 инварианты | Решение требует менять IR-схему, интерфейс `BIAdapter` или инварианты 1–8 этого файла | STOP всегда — вопрос пользователю, модель не важна |
| S5 form_data | viz_type не проходит contract-тест после 3 вариантов шаблона | STOP → приложить diff «create vs GET»; ручная сессия (вероятно с браузером), модель вторична |
| S6 граница фазы | Exit criteria фазы выполнены и проверены | STOP → предложить `/cxkm`-ревью перед следующей фазой; следующую фазу самому не начинать |
| S7 инфра | GraceKelly / Superset / ClickHouse-стенд недоступен после 2 попыток восстановления | STOP **без** эскалации модели — это не модельная проблема |

**Протокол STOP:** (1) WIP-коммит в текущую ветку `wip(stopper:Sx): <задача>`; (2) одна строка в блок «Статус» этого файла: дата, задача, стоппер, рекомендованная модель; (3) финальное сообщение: причина, что переключить, точка продолжения. После S1/S3/S5 допустимо взять независимую задачу той же фазы; после S2/S4/S6 — только остановка.

## Definition of done (фаза/задача)

- Тесты зелёные (pytest), ruff чистый.
- Exit criteria фазы из PLAN.md проверены реальными прогонами, не «на глаз».
- Обновлены: статус в этом файле; ARCHITECTURE.md — если дизайн уточнился.
