# CLAUDE.md — Auto_BI_new (DecisionOps)

> **Это форк, и продукт здесь другой.** `D:\Auto_BI_new` — не «текст → дашборд», а
> **Analytics Release & Evidence Gate**: проверка, что изменение семантики не ломает
> governed-числа, с content-addressed evidence. v1-раннтайм Auto_BI сохранён без
> функциональных изменений и всё ещё описан ниже — правила v1 (инварианты, стопперы,
> DoD) в силе для кода, который их касается.
>
> **Вход в сессию — `docs/DECISIONOPS_CURRENT_STATE.md`** (состояние и открытые
> решения), затем `docs/DECISIONOPS_ARCHITECTURE.md` (дизайн и обоснования).
> Завершённый bootstrap и подробный журнал лежат в `docs/history/`; читать их
> только когда нужна история решения. `_NEXT_SESSION.md` из исходного репозитория
> сюда **не переносился** — не искать.

## Актуальный порядок работы

Решение владельца 2026-09-12 закреплено в [AGENTS.md](AGENTS.md): простые задачи —
Luna xhigh; содержательное ревью — только моделирование через Astra xhigh;
инфраструктурные стенды — только Mac. Старые модельные стопперы ниже — история
baseline, не действующий порядок эскалации. Человеческое ревью не требуется для
продолжения разработки; модельная оценка не становится human evidence.

## Статус v1 (унаследованное)

> Пооперационный лог сессий и разборы (`docs/history/`, `docs/plans/`) вынесены во
> внутренний репозиторий: это рабочая кухня — стенды, машины, ходы отладки, — и она
> устаревала быстрее, чем окупалась её правка. Всё, что имеет продуктовое значение,
> живёт в поддерживаемых доках: `docs/ARCHITECTURE.md` (дизайн и его обоснования),
> `docs/PLAN.md` (фазы), `CHANGELOG.md` (что и когда поехало).
>
> v1-контекст: агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя
> DWH — `README.md`, `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `docs/MARKET.md`.

## Скоуп (решение 2026-06-11)

RU-рынок. **v1 = ClickHouse (DM) + Superset (BI)**; v2 = Greengage/Greenplum + DataLens (Public API). Power BI / Tableau / Metabase — вне скоупа. Универсальность держим в швах (IR, `BIAdapter`, rule pack per engine), имплементируем один путь. Демо-DM — на ClickHouse (docker).

## Инварианты дизайна (не нарушать без обновления ARCHITECTURE.md)

1. **IR-first**: LLM генерирует только `DashboardSpec` (pydantic IR). Нативные форматы BI (form_data, конфиги DataLens) — только детерминированный код адаптеров.
2. Spec валидируется против `semantic/model.yaml` ДО любых вызовов BI; неизвестное поле → reject + repair loop (max 3), никаких молчаливых починок.
3. SQL: только SELECT (sqlglot-guard), EXPLAIN + LIMIT-прогон, read-only роль DWH.
4. Уточняющие вопросы — только из grounding report, ≤3 за раунд; однозначный запрос → ноль вопросов.
5. **Feasibility Advisor**: вердикты только из детерминированных findings (ключи/статистика/EXPLAIN) — LLM формулирует, но не «решает»; advisory-only, сборку никогда не блокирует; классы `ok / spec_adjustment / dm_change_request`.
6. **Fields-first** (drag&drop полей) — второй вход в тот же пайплайн (seed для GROUNDING), не отдельный конструктор чартов.
7. Версия Superset запинена в docker-compose; версия DataLens-стенда (контрактные маркеры: gateway v4.10.4, dash schemeVersion=8, chart shared version "4", HC=1, gateway-экшен `us/renameEntry` с entryId-стабильностью для атомарного rebuild) запинена в runbook `internal/2026-06-13-datalens-selfhosted-runbook.md`; обновление любой — отдельная задача с прогоном contract-тестов (Superset viz-сьют / `tests/test_datalens_contract.py`).
8. Изменения промптов мержатся только с прогоном eval-сьюта (с Phase 1).

## LLM

GraceKelly (локальный сервис, должен быть запущен): `POST http://127.0.0.1:8011/api/v1/orchestrate`
`{"prompt": ..., "model": "claude-sonnet-5", "reasoning": true, "decompose": false, "session_id": ..., "metadata": {"app": "auto_bi"}}`
- `prompt` ≤ 40 000 символов → context selection обязателен на больших DM.
- Text-in/text-out: структурированный вывод через JSON-блок → pydantic → repair loop.
- Все вызовы через `llm/LLMClient`-абстракцию, напрямую httpx из бизнес-кода не дёргать.
- `reasoning: true` — на GROUNDING/PROPOSE_SPEC; механические шаги — без reasoning.

## Stack и конвенции

- Python 3.12 + uv; ruff + black; pytest. FastAPI, pydantic v2, sqlglot, httpx.
- Без LangChain/LangGraph — агент = простая state machine.
- Код/идентификаторы — английский; доки и общение — русский.
- Секреты только в `.env` (в `.gitignore`). Единственное документированное исключение —
  DecisionOps Ed25519 signing key: explicit PKCS#8 path вне checkout/evidence, на Mac с
  mode `0600`. Значения секретов в логи/доки не попадают.
- Стенд только на Mac: `docker-compose up` (Superset + ClickHouse с демо-DM).

## Исторические стопперы переключения модели (заменены AGENTS.md)

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

## Статус

Журнал срезов — [docs/history/DECISIONOPS_STATUS_LOG.md](docs/history/DECISIONOPS_STATUS_LOG.md):
запись среза пишется туда, сверху, полным текстом. До 2026-10-05 журнал вёлся здесь и
грузился в каждую сессию целиком; читать его, только когда нужна история решения. Здесь —
три последних среза, каждый не длиннее пяти строк; с новой записью нижняя убирается.
Состояние и открытые решения — `docs/DECISIONOPS_CURRENT_STATE.md`.

- 2026-10-22 — T-72: Unicode в ошибках argparse на strict stderr получает escape,
  exit остаётся 2; usage/version и прочие ошибки записи сохраняют контракт. Версии 98/73.
  Смежные проверки: 282 passed; Codex; synthetic Astra xhigh: 0 P1/P2, не human acceptance.
  [Проверки и границы](docs/DECISIONOPS_PARSER_DIAGNOSTICS_CHECKS_2026-10-22.md).
- 2026-10-22 — T-71: CLI-справка под ASCII/Latin-1 печатает escape вместо traceback,
  exit 0; остальные parser messages и обработчики сохранены. Версии 98/73.
  Смежные проверки: 244 passed; Codex; synthetic Astra xhigh: 0 P1/P2, не human acceptance.
  [Проверки и границы](docs/DECISIONOPS_HELP_ENCODING_CHECKS_2026-10-22.md).
- 2026-10-22 — T-70: отказ runtime-настройки ClickHouse даёт `cost.not_measured`, без
  ложного превышения. На Mac проверены readonly=1/2 и настоящие byte/timeout превышения.
  Версии 98/73; 368 passed. Codex; synthetic Astra xhigh: 0 P1/P2, не human acceptance.
  [Проверки и границы](docs/DECISIONOPS_RUNTIME_SETTINGS_CHECKS_2026-10-22.md).

## Definition of done (фаза/задача)

- Тесты зелёные (pytest), ruff чистый.
- Exit criteria фазы из PLAN.md проверены реальными прогонами, не «на глаз».
- Обновлены: запись среза в журнале статуса (`docs/history/DECISIONOPS_STATUS_LOG.md`) и
  короткая запись в «Статус» этого файла; ARCHITECTURE.md — если дизайн уточнился.
