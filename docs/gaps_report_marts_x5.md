# Gaps report

Таблиц в модели: 3 — marts.branch_pnl, marts.customer_360, marts.returns_velocity

Findings: 1 critical / 8 warn / 3 info

## critical
- **no_relationships**: ни одной связи между таблицами не обнаружено
  - Таблицы изолированы: запрос с полями из разных таблиц невозможен. Нужны FK-конвенции (*_id -> справочник) или ручные joins в model.yaml.

## warn
- **column_all_null**: `marts.customer_360.last_visit_at` — колонка целиком NULL — источник не наполняет её
  - Поле есть в схеме, но данных нет: фильтры и разрезы по нему пусты.
- **column_all_null**: `marts.customer_360.loyalty_source` — колонка целиком NULL — источник не наполняет её
  - Поле есть в схеме, но данных нет: фильтры и разрезы по нему пусты.
- **column_all_null**: `marts.customer_360.pii_source` — колонка целиком NULL — источник не наполняет её
  - Поле есть в схеме, но данных нет: фильтры и разрезы по нему пусты.
- **preaggregated_time_grain**: `marts.branch_pnl.month` — временная колонка агрегирована до «month»
  - Дневная динамика и нестандартные периоды по этой таблице невозможны.
- **preaggregated_time_grain**: `marts.returns_velocity.week` — временная колонка агрегирована до «week»
  - Дневная динамика и нестандартные периоды по этой таблице невозможны.
- **table_no_description**: `marts.branch_pnl` — у таблицы нет описания — grounding опирается только на имя
- **table_no_description**: `marts.customer_360` — у таблицы нет описания — grounding опирается только на имя
- **table_no_description**: `marts.returns_velocity` — у таблицы нет описания — grounding опирается только на имя

## info
- **columns_no_description**: `marts.branch_pnl` — без описания 11 из 11 колонок
  - branch, month, orders, gross_revenue, tax_collected, net_revenue, discounts, shipping, returned_orders, returned_value, effective_tax_rate
- **columns_no_description**: `marts.customer_360` — без описания 18 из 18 колонок
  - customer_hk, customer_bk, branch, first_name, last_name, email, loyalty_segment, loyalty_points, last_visit_at, pii_source, loyalty_source, order_count, lifetime_value, first_order_dt, last_order_dt, returned_orders, returned_value, return_rate
- **columns_no_description**: `marts.returns_velocity` — без описания 8 из 8 колонок
  - branch, channel, week, orders, returned_orders, return_rate, returned_value, returned_tax_unrecovered

## Кандидаты в dm_change_request
- `DM` (no_relationships): ни одной связи между таблицами не обнаружено
- `marts.customer_360.last_visit_at` (column_all_null): колонка целиком NULL — источник не наполняет её
- `marts.customer_360.loyalty_source` (column_all_null): колонка целиком NULL — источник не наполняет её
- `marts.customer_360.pii_source` (column_all_null): колонка целиком NULL — источник не наполняет её
