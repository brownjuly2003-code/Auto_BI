# CLAUDE.md — Auto_BI

Агент «текст/раскладка полей → дашборд в выбранной BI» поверх DM-слоя DWH. Полный контекст: `README.md`, `docs/ARCHITECTURE.md` (дизайн), `docs/PLAN.md` (фазы), `docs/MARKET.md` (рынок/зачем).

## Статус

> **СОСТОЯНИЕ СЕЙЧАС (2026-07-04, по «Auto_BI, plan.md. S10 - возьми в работу» из `plan.md`): S10 — DOCKER + RELEASE-КОНВЕЙЕР, ГОТОВО (тег/публикация не вырезаны).** Закрывает D-1/D-2/T-3 из `audit_fable_03_07_26.md`. Новый job `docker` в `ci.yml` (параллельно `quality`/`integration`) собирает образ на каждый push/PR — build-only, ловит дрейф `Dockerfile` (ни разу не собирался — D-2). Новый workflow `release.yml`, триггер только на push тега `vX.Y.Z`: собирает+пушит образ в GHCR (`ghcr.io/<repo, lowercase>:<version>`+`:latest`, через `GITHUB_TOKEN`, без доп. секретов) и создаёт GitHub Release из секции `CHANGELOG.md`, вырезанной `awk`. `auto_bi --version` (argparse `action="version"`) + поле `version` в `GET /api/v1/health` — оба читают `auto_bi.__version__` (единственный источник, бампится вручную; CI ничего не автобампит). Версия 0.1.0→**0.2.0** (`pyproject.toml`+`__init__.py`). Новый `CHANGELOG.md` (Keep a Changelog) — секция `[0.2.0]` документирует накопленный функционал Phase 0–4+hardening (первый версионированный релиз проекта — `git tag`/Releases были пусты с основания, D-1). Coverage-бейдж теперь генерируется CI, а не правится руками (T-3/D-3): шаг в `quality`-job читает `.coverage` (`coverage report --format=total`), пишет `.github/badges/coverage.json` (shields.io endpoint-схема); README ссылается через `img.shields.io/endpoint`. Коммит бейджа обратно в main — отдельный шаг, гейтится `push`+`main` (никогда PR) и пропускается без изменений (`git diff --quiet`, `[skip ci]`); `quality`-job получил `permissions: contents: write` только под этот шаг. Гейт: ruff/black/mypy 0/68 · pytest **624**/1 skipped(нет `anthropic`-extra, ожидаемо)/32 deselected, 95% cov (+1: `--version`-тест) · advisor-eval 9/9. Доки: ARCHITECTURE §3.13 (новая), README (динамический бейдж, ссылка CHANGELOG, абзац docker/release). **Верификация без локального Docker (Windows-машина не гоняет Docker):** ветка запушена в PR #7, живой прогон на GH Actions ubuntu-latest — все 3 job'а (`docker` 18с, `integration` 1м26с, `quality` 34с) зелёные. PR смержен ff в main (`452a8d5`), push-триггер CI на main тоже зелёный (run 28695238500); шаг коммита бейджа корректно отработал no-op (95%→95%, изменений нет — «Badge unchanged, nothing to commit»). Git status перед стартом был чист (main = origin/main = `deebbe9`), конкурентных сессий не было.
>
> **⚠️ НЕ сделано намеренно:** тег `v0.2.0` не вырезан, `release.yml` ни разу не выполнялся живьём — первый публичный релиз проекта (первый образ под аккаунтом владельца в GHCR, первый GitHub Release) вынесен на явное подтверждение владельца, хотя S10 в `plan.md` без гейта. Как только владелец скажет «да»: `git tag v0.2.0 && git push origin v0.2.0` из `main`=`452a8d5` (или новее) — `release.yml` подхватит сам, вручную ничего собирать не нужно.
>
> Полная история предыдущих сессий (S02, S04, S06, S07, S08, S09, cont.5–cont.16, Phase 0–1) — `docs/history/claude-status-log.md`.

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
