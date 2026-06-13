# Phase 3 — Greenplum/Greengage engine track (runbook + state)

Дата: 2026-06-13. Engine-трек Phase 3 (второй DWH) реализован и **live-валидирован**
на одиночном Greenplum 6.25 на Mac. Влито в `main` (merge `ce9ff2c`).
ClickHouse-путь не тронут; pytest 237 / ruff / black clean.

## Что сделано (3.3 + 3.4, demo-уровень)

| Слой | Файл | Live-валидация на GP-стенде |
|---|---|---|
| dialect seam | `auto_bi/engine.py`, `agent/sqlgen.py`, `agent/sql_guard.py` | сгенерённый postgres-SQL (agg/top-N, date-фильтр, co-located & motion joins, Cyrillic alias) исполнился на GP |
| introspector | `auto_bi/introspect/greenplum.py` | интроспекция `dm` → `semantic/model_gp.yaml` (distribution key, date-партиция, rows=300k, роли, n_distinct, top_values) |
| advisor pack | `auto_bi/advisor/greenplum.py`, `advisor/core.py` | motion-join → `non_colocated_join` (EXPLAIN: Broadcast/Redistribute Motion); фильтр не по партиции → `partition_not_pruned` (7 of 7); co-located + date-фильтр → clean |
| Physical | `semantic/model.py` | `distribution_key` (аддитивно) |

Greengage = форк Greenplum (те же каталоги `gp_distribution_policy`, `pg_partition`) → один code path.

## Стенд (Mac `deproject-mac`, Colima vz, 6GiB VM)

- Образ: `andruche/greenplum:6` (GP 6.25.3, PG9.4-base). Контейнер `auto_bi_greenplum`,
  **порт хоста 5433** (НЕ 5432 — на Mac занят нативным PostgreSQL; Colima не пробрасывает 5432).
- Поднять: `docker run -d --name auto_bi_greenplum -p 5433:5432 andruche/greenplum:6` → ~25с init.
- DM: `scripts/stand_create_gp_dm.sql` → `docker cp` + `psql -f` от gpadmin. Звезда sales/stores/products,
  `DISTRIBUTED BY`, месячные RANGE-партиции, 300k строк факта, роль `auto_bi_ro` (пароль `ro_pw`).
- **pg_hba quirk**: образ дописывает trust catch-all, но он не подхватывается для туннельного источника →
  внешний коннект идёт по md5. Использовать `auto_bi_ro`/`ro_pw` (или gpadmin с заданным паролем).
  `psql` доступен только в login-shell gpadmin: `docker exec -u gpadmin … bash -lc 'psql …'`.
- Доступ с Windows: туннель `ssh -N -L 15433:localhost:5433 deproject-mac`, затем
  `AUTO_BI_GP_HOST=127.0.0.1 AUTO_BI_GP_PORT=15433 AUTO_BI_GP_USER=auto_bi_ro AUTO_BI_GP_PASSWORD=ro_pw`.
- Память: GP ~0.7GiB, рядом живёт auto_bi_stand (CH+Superset) — не трогать.

## Воспроизвести live-валидацию

```bash
# tunnel up, then:
AUTO_BI_GP_HOST=127.0.0.1 AUTO_BI_GP_PORT=15433 AUTO_BI_GP_USER=auto_bi_ro AUTO_BI_GP_PASSWORD=ro_pw \
  .venv/Scripts/python.exe -c "from auto_bi.config import get_settings; \
  from auto_bi.introspect.greenplum import GreenplumIntrospector, make_run_query_pg; \
  m=GreenplumIntrospector(make_run_query_pg(get_settings()), schema='dm').introspect(); \
  m.dump('semantic/model_gp.yaml'); print([t.name for t in m.tables])"
```

## Не сделано / остаток (только по явному «go»)

- **Масштаб**: demo-факт 300k (< 10M порогов) — `distribution_skew`/`no_filter_on_large_fact`
  unit-тестируются, но live-демо на demo не срабатывает (нужен факт ≥10M на GP).
- **Greengage-специфика**: проверено на Greenplum 6.25 (форк-совместимо); прогон на самом Greengage не делался.
- **eval (3.5)**: golden-кейсы на GP-демо не добавлялись (инвариант 8 — нужен live GraceKelly; делать отдельно).
- **DataLens (3.1/3.2)**: блокировано (нужен IAM/workbook+HC) — см. `2026-06-13-phase3-prep.md`.
- **multi-level/list-партиции, REPLICATED dims на скейле, motion на large-large join** — не покрыты на demo.
