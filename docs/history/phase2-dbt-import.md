# Task 2.6 — dbt-импорт (manifest/catalog → descriptions, relationships)

## Goal
Обогащение существующего `model.yaml` из dbt-артефактов: описания таблиц/колонок и relationships→joins/fk. dbt — НЕ источник схемы (схему владеет интроспектор); merge-политика — «заполнять только пустое», ручные правки всегда выигрывают.

## Tasks
- [x] 1. `auto_bi/semantic/dbt_import.py`: `dbt_enrich(model, manifest, catalog=None) -> DbtImportReport` — маппинг node→`schema.alias`; fill-empty: table/column descriptions (manifest, фоллбек catalog comment, case-insensitive); relationships-тесты → joins (дедуп) + fk колонки; unmatched models/columns в отчёт → Verify: tests/test_dbt_import.py (7 тестов)
- [x] 2. CLI `auto_bi dbt-import --manifest … [--catalog …] [--model-path …] [--dry-run]`: применяет merge, пишет model.yaml, печатает детерминированный отчёт → Verify: CLI-тесты во временной папке (dry-run не пишет, запись перечитывается, второй прогон «Изменений нет»)
- [x] 3. Docs: CLAUDE.md статус, ARCHITECTURE §3.1 (политика «dbt = enrichment, не схема») → Verify: diff

## Done When
- [x] pytest зелёный (188 passed), ruff/black clean
- [x] dry-run и запись работают на тестовом манифесте; повторный прогон идемпотентен (0 изменений)
