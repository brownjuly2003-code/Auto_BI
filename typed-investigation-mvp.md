# Typed investigation MVP

## Goal

Добавить offline-срез typed investigation, который превращает канонические результаты
в проверяемые observations и Markdown-блоки без causal wording, LLM и удалённых мутаций.

## Tasks

- [x] Зафиксировать owner-решение: synthetic review разрешает только offline alpha; human/design-partner gate остаётся перед continuous runtime → Verify: architecture/current-state говорят одно и то же.
- [x] Добавить red contract tests для `InvestigationPlan v0`: strict schema, pinned metrics, versioned skills, budgets, stop/escalation policy, scope confinement и запрет causal claims → Verify: новый focused test падает до реализации.
- [x] Реализовать `auto_bi.investigation` contract model и YAML round-trip/digest → Verify: contract tests зелёные, unknown fields fail closed.
- [x] Реализовать четыре deterministic skills (`data_health`, `period_variance`, `contribution_decomposition`, `concentration_shift`) на synthetic truth sets → Verify: decomposition сходится к total delta, unsupported scope даёт typed gap.
- [x] Скомпилировать outputs в evidence-backed claims и deterministic Markdown blocks → Verify: у каждого factual claim есть evidence ref, causal wording отсутствует, одинаковый input даёт одинаковые bytes.
- [x] Добавить минимальный offline application/CLI path без DWH/BI mutation → Verify: fixture run создаёт plan/results/brief artifacts; mutation spy остаётся пустым.
- [x] Обновить нормативную архитектуру и current state фактическими границами среза → Verify: docs-as-code focused tests и `git diff --check` зелёные.
- [ ] Финальная проверка → Verify: focused investigation/decision/evidence suite, Ruff и Black check проходят.

## Done When

- [ ] Typed plan исполняет только объявленные v1 skills в пределах budget/scope.
- [ ] Все observations и brief facts детерминированы и адресуют evidence.
- [ ] Unsupported/ambiguous inputs дают честный gap, а не guessed result.
- [ ] Continuous runtime и human outcome не объявлены доказанными.
