# Superset inventory fixtures

Dashboards 1-11 are Apache Superset 4.1.2 `superset load-examples` (Apache License 2.0).
Dashboard 12 is a dashboard made on the project's stand over the demo DM.

Each `dashboard_<n>.json` is the `result` of the three GETs (`/api/v1/dashboard/{id}`, `/charts`,
`/datasets`), trimmed to the fields the inventory reads, and `slug`, by which the tests' recorded
source finds a dashboard:

- dashboard: `id`, `dashboard_title`, `slug`, `changed_on`, `json_metadata` (no dashboard of the
  set has a native filter);
- chart: `id`, `slice_name`, `form_data` without `dashboards` and `url_params`;
- dataset: `id`, `schema`, `table_name`, `sql`, `database` (`id`, `backend`), `columns`
  (`column_name`, `expression`, `is_dttm`, `type`), `metrics` (`metric_name`, `expression`).

`e4a/<run>.json` is the pinned replay corpus of E4b (ADR 0013): the E4a stand records
(`docs/pilot/2026-11-18-e4a-rendered-sql`, Superset 4.1.2 and 6.1.0; C1 is `load-examples`, C2 the
project's own charts over the demo DM), reduced to the answers the inventory can get by `GET` — the
three GETs above, the chart list rows (`changed_on_utc`, `last_saved_at`), `GET chart/{id}` without
`dashboards` and `GET chart/{id}/data?type=query` — and the reference: the SQL the dashboard showed
for each tile in a browser, with whether the engine ran each statement. User fields and result
data are dropped. `scripts/superset_inventory_measure.py` reads it; the digests are pinned in
`tests/test_superset_inventory_corpus.py`.
