# P1-6 — Semantic governance: rates/non-additive + честный знаменатель advisor'а

## Goal

Семантическая модель начинает защищать бизнес-корректность: rate/ratio-колонки не суммируются
(audit P1-6: `effective_tax_rate`/`return_rate` с `agg: sum` в committed-моделях), а advisor
не делит live `EXPLAIN ESTIMATE` на устаревший статический `physical.rows` (ложная доля скана,
значения >100% при дрейфе окружения 1M/20M/100M).

## Tasks

- [ ] 1. Схема: `Additivity` enum + `Column.additivity` (optional) + `Physical.captured_at`
      → Verify: round-trip dump/load, None-поля не попадают в yaml.
- [ ] 2. Интроспекторы CH+GP: rate-паттерн имени (`rate|ratio|pct|percent|share`, `price`/`unit_price`)
      → `agg: avg` + `additivity: non_additive`; штамп `captured_at` (UTC ISO)
      → Verify: юнит-тесты на фейковом run_query.
- [ ] 3. Валидация спеки (`_check_measure_col`): `sum` над `non_additive` колонкой → ошибка
      (repair loop получает подсказку «avg или ratio numerator/denominator»); покрывает и denominator
      → Verify: тест error/ok.
- [ ] 4. Autospec: дефолт agg для non_additive без agg = AVG; share-чарт (P4) только для
      аддитивной primary-меры (`is_additive_agg` helper в ir/spec, переиспользован в
      `is_compact_number`) → Verify: тест «нет share для avg-primary».
- [ ] 5. Render: маркер additivity в промпт-рендере колонки (replay ключуется по step/schema —
      безопасно) → Verify: тест на строку.
- [ ] 6. Advisor (CH): live row count из `system.tables` через RunQuery (кэш на инстанс, never-raise)
      → `explain_high_scan_fraction` предпочитает live-знаменатель, fallback = `physical.rows`;
      evidence несёт `total_rows_source` → Verify: тест stale-20M/live-1M → доля 0.9, source=live;
      fallback-тесты. GP scan-fraction правила не имеет — без изменений.
- [ ] 7. Enrichment API: PATCH `agg=sum` на non_additive → 422; `additivity` в ответах/fields
      → Verify: тест 422/200.
- [ ] 8. Committed-модели: `model.yaml` price→avg+non_additive; `model_stand.yaml` price→то же,
      `customers` (uniqExact-снэпшот) → `semi_additive` (записано, НЕ энфорсится в v1);
      `model_x5.yaml` effective_tax_rate + return_rate×2 → avg+non_additive
      → Verify: pytest целиком зелёный (fixtures суммируют только аддитивные колонки — проверено).
- [ ] 9. Доки: ARCHITECTURE §3.2 (additivity, эвристика, governance-гейт) + §3.3 (live-знаменатель,
      captured_at); CHANGELOG §Unreleased.
- [ ] 10. Гейты + live: ruff/black/mypy · pytest · advisor 9/9 · replay 37+16; live-проверка на
      CH-стенде мака (заведомо устаревший rows в модели → advisor берёт live) — стенд вернуть
      погашенным. Ветка `fix/p1-6-semantic-governance` → PR → CI → merge.

## Done When

- [ ] `sum` над rate-колонкой невозможен молча: интроспектор не предлагает, валидация отклоняет,
      UI-обогащение отвечает 422.
- [ ] Advisor не выдаёт долю скана от чужого окружения: live-знаменатель при наличии RunQuery.
- [ ] Все гейты зелёные, PR вмёржен.

## Notes

- `semi_additive` — записываемое, но не энфорсируемое значение (v1): энфорс требует знания оси
  неаддитивности; включать по мере появления реальных semi-additive витрин.
- Полная рекомендация аудита (owner/PII/SLA/контрольные значения) — сознательно вне скоупа
  этой сессии; скоуп = rates/non-additive + stats freshness (заголовок P1-6 в хэндоффе).
