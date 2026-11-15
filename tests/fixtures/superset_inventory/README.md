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
