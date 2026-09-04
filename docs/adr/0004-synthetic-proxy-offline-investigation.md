# ADR 0004: synthetic proxy разрешает только offline investigation alpha

**Статус:** Accepted
**Дата:** 2026-09-04
**Связано:** [текущее состояние](../DECISIONOPS_CURRENT_STATE.md),
[целевая архитектура](../DECISIONOPS_ARCHITECTURE.md),
[симуляция owner-gate](../DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-04.md)

## Context

Verifier 7 прошёл технический pilot на реальном semantic change и двух продуктах
против ClickHouse на Mac. Proxy-review правильно восстановил verdict, affected scope,
точное изменение и owner action, но не дал человеческого time-to-decision или
design-partner evidence.

Владелец разрешил самостоятельно принять scoped product-решение с моделированием и
исследованием. Исследование задаёт жёсткую границу этому разрешению:

- GOV.UK определяет usability testing как наблюдение за actual или likely users,
  выполняющими правдоподобные задачи без подсказки ответа;
- Nielsen Norman Group рекомендует synthetic users для desk research и генерации
  гипотез, но не как замену real research или основание финального решения;
- оценка времени по скорости чтения остаётся моделью, а не stopwatch measurement
  конкретного пользователя.

Источники: [GOV.UK — Using moderated usability testing](https://www.gov.uk/service-manual/user-research/using-moderated-usability-testing),
[NN/g — Synthetic Users](https://www.nngroup.com/articles/synthetic-users/),
[Brysbaert, 2019 — reading-rate meta-analysis](https://doi.org/10.1016/j.jml.2019.104047).

## Decision

1. Не объявлять Task 8, human acceptance или design-partner evidence закрытыми.
2. Принять v7 proxy-review как достаточный только для старта внутреннего offline alpha:
   `InvestigationPlan v0`, deterministic investigation skills и evidence-backed
   Markdown blocks.
3. Этот срез работает на fixtures/canonical results, не планирует фоновые jobs, не
   мутирует DWH/BI и не отправляет уведомления или actions.
4. Его технический gate: deterministic synthetic truth sets, ноль causal claims, ноль
   factual claims без evidence, scope/budget fail-closed и воспроизводимые bytes.
5. Scheduler, PostgreSQL runtime, REST/MCP, notifications, actions и production-ready
   claims остаются заблокированы до наблюдаемого теста с actual/likely user и
   design-partner evidence.
6. Для offline alpha сохраняются локальный `content_hashed` trust level, отсутствие
   remote и текущая package identity; подпись, публикация и rename не требуются.

## Consequences

Положительные:

- работа продолжается в проверяемом offline-контуре без подмены human evidence;
- следующий срез проверяет главный trust-риск brief: факт без evidence и causal wording;
- инфраструктурные и внешние поверхности не опережают продуктовую валидацию.

Стоимость и ограничения:

- synthetic task success не измеряет usefulness, доверие или реальное время решения;
- слово `certified` относится только к выполненным deterministic gates и evidence,
  а не к пользовательскому принятию;
- переход к continuous platform всё ещё требует внешнего наблюдаемого gate.

## Alternatives considered

### A. Считать synthetic review человеческим sign-off

Быстро закрывает чекбокс, но противоречит методике usability research и создаёт
ложное evidence. Отклонено.

### B. Полностью остановить локальную разработку до design partner

Методологически безопасно, но блокирует независимо проверяемый offline contract slice.
Отклонено в пользу ограниченного alpha.

### C. Сразу строить scheduler/MCP/actions

Не проверяет ценность brief и расширяет operational risk. Отклонено.

## Verification

- normative architecture и current state одинаково описывают границу proxy gate;
- focused contract/truth-set tests доказывают offline ограничения;
- human/design-partner outcome остаётся явно открытым и нигде не называется measured.
