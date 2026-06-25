# Advisor → рецепт: remediation-артефакт в заявке владельцу DM

Дата: 2026-06-25. Ветка: `feat/advisor-remediation` (от `quality/mypy-strict-green`).

## Goal
Превратить вердикт класса `dm_change_request` из «вот проблема, просим оценить» в
«вот проблема + готовый артефакт-решение (DDL/денормализующая витрина), отдай DM-инженеру».
Углубляет единственный-в-мире дифференциатор №3, детерминированно, без стенда.

## Принцип (инвариант D9 не нарушается)
Вердикт и **сам артефакт решения** генерирует КОД (правило), детерминированно из
physical-метаданных. LLM по-прежнему только нарратив — remediation он не сочиняет.
Advisory-only: remediation появляется лишь у `dm_change_request`-находок, build не блокируется.

## Tasks
- [x] T1 `findings.py`: модель `Remediation {kind, summary, ddl, rationale}` + `Finding.remediation: Remediation | None = None` → Verify: импортируется, pydantic-валидна.
- [x] T2 `clickhouse.py`: ветка `dm_change_request` в `filter_not_in_sorting_key_prefix` отдаёт remediation = ClickHouse `ADD PROJECTION ... ORDER BY <filter cols>` + `MATERIALIZE` → Verify: unit на off-key фильтре.
- [x] T3 `clickhouse.py`: новое правило `join_large_large` (реактивация отложенного — джойны давно есть) → remediation = денормализующая dbt-витрина; требует `RuleContext.model` → Verify: unit (большой×большой джойн → finding+remediation; маленький dim → молчит).
- [x] T4 `greenplum.py`: `distribution_skew` отдаёт remediation = `ALTER TABLE ... SET DISTRIBUTED BY (<higher-card>)` → Verify: unit на low-card dist key.
- [x] T5 `core.py`: прокинуть `model` в `RuleContext` (опц., default None — старые правила не трогаются) → Verify: существующие advisor-тесты зелёные.
- [x] T6 `narrate.py`: `ChartVerdict.remediations: list[Remediation]`; `worst_verdicts` собирает их из findings → Verify: unit (несколько находок → собранные remediations).
- [x] T7 `store/db.py`: schema v4 — колонка `remediation TEXT` в `dm_change_requests` + идемпотентная миграция (`_add_column`) + `add_dm_change_request(remediation=...)` → Verify: миграция v3→v4 на legacy-БД, round-trip.
- [x] T8 `dmcr.py`: `render_remediation()` (markdown ```sql секция) встроена в `render_dm_change_request` (читает row['remediation'] JSON) → Verify: unit (заявка с DDL / без — деградирует).
- [x] T9 `machine.py`: сериализовать remediation(s) DCR-вердикта в store при сохранении → Verify: интеграционный unit на machine.
- [x] T10 Полный гейт: mypy 0 / pytest / ruff / black → Verify: все 4 зелёные локально.

## Non-goals (осознанно)
- `point_lookup_pattern` — PLAN 1.6 сам помечает «no real case to tune against»; не плодим
  false-positive без реального кейса. Остаётся отложенным.
- Live-сборка на стенде не требуется (фича чисто в IR/advisor/store-слое, офлайн-тестируема).

## Done When
- [x] `dm_change_request`-заявка для CH (off-key фильтр / large-large join) и GP (dist skew)
  несёт готовый исполняемый DDL/SQL-артефакт.
- [x] Все 4 гейта зелёные; +unit-покрытие на каждое правило и рендер.
- [x] Инварианты 1–8 не тронуты (LLM не генерирует remediation; advisory-only).
