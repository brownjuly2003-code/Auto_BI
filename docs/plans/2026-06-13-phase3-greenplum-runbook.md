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

## Воспроизвести live-валидацию НА СКЕЙЛЕ (≥10M) — at-scale advisor-правила

Два правила гейтятся `physical.rows >= 10M` и на 300k-демо не срабатывают:
`no_filter_on_large_fact` (CRITICAL) и `distribution_skew` (WARN, DCR). Скейл — opt-in
(канонический демо остаётся 300k для быстрых ребилдов; `semantic/model_gp.yaml` коммитится из 300k).

```bash
# 1) долить факт до ~10.1M (после stand_create_gp_dm.sql). На 8GB Mac — батчами по 2M
#    через stdin (heredoc), память реклеймится; один INSERT 9.8M тоже ок (стрим, диск-spill).
cat scripts/stand_scale_gp_dm.sql | \
  ssh deproject-mac "/usr/local/bin/docker exec -i -u gpadmin auto_bi_greenplum \
    bash -lc 'psql -p 5432 -d postgres -v ON_ERROR_STOP=1'"
# 2) tunnel up, затем live-интроспекция + прогон advisor (dump в .tmp/, демо-yaml не трогается):
AUTO_BI_GP_HOST=127.0.0.1 AUTO_BI_GP_PORT=15433 AUTO_BI_GP_USER=auto_bi_ro \
  AUTO_BI_GP_PASSWORD=ro_pw uv run python scripts/gp_scale_validate.py
```

Результат (live, 2026-06-13, dm.sales = 10 300 000 строк, store_id n_distinct=20 из pg_stats):
- интроспектор просуммировал reltuples партиц-детей → `rows: 10300000` (`>= 10M`), `distribution_key=['store_id']`;
- **chart A (без фильтра)** → `distribution_skew` (WARN/dm_change_request: «~20 combinations → uneven spread … on 10300000 rows»)
  **+** `no_filter_on_large_fact` (CRITICAL/spec_adjustment: «dm.sales has 10300000 rows and the query has no filters»);
- **chart B (date-фильтр ≥ 2026-04-01)** → только `distribution_skew` (фильтр снял `no_filter_on_large_fact`).

Откат к 300k: повторно прогнать `stand_create_gp_dm.sql` (DROP SCHEMA CASCADE + rebuild).

## Multi-level / list-партиции (интроспектор)

`introspect/greenplum.py::_partition_key` читает партиц-колонки со ВСЕХ уровней
(`pg_partition`, фильтр `paristemplate=false`, ORDER BY `parlevel`), не только `parlevel=0`.
Двухуровневая таблица RANGE(date)→LIST(region) даёт `partition_key='date, region'`;
одноуровневая — `'date'` (обратная совместимость); непартиционированная — `''`.
Реверс структуры каталога — с живого GP: `pg_partition` несёт по одной non-template
строке на уровень (`paratts` = int2vector колонок уровня) + template-строку на
SUBPARTITION TEMPLATE (её исключаем). Live-фикстура (после прогона дропнута со стенда):

```sql
CREATE TABLE dm.sales_ml ("date" date NOT NULL, region text, store_id int, revenue numeric(12,2))
DISTRIBUTED BY (store_id)
PARTITION BY RANGE ("date") SUBPARTITION BY LIST (region)
  SUBPARTITION TEMPLATE ( SUBPARTITION r_msk VALUES ('msk'),
                          SUBPARTITION r_spb VALUES ('spb'),
                          SUBPARTITION r_other VALUES ('other') )
( START (date '2026-01-01') INCLUSIVE END (date '2026-04-01') EXCLUSIVE EVERY (interval '1 month') );
```

Live-результат (2026-06-13): `dm.sales_ml → partition_key='date, region'`, `dm.sales → 'date'`.
Юнит-тест `test_partition_key_multi_level_ordered_by_level` фиксирует логику.

## Не сделано / остаток (только по явному «go»)

- ~~**Масштаб**: `distribution_skew`/`no_filter_on_large_fact` на demo не срабатывали (< 10M).~~
  ✅ 2026-06-13: live-валидировано на 10.3M (см. секцию выше); `scripts/stand_scale_gp_dm.sql`
  + `scripts/gp_scale_validate.py`. Канонический демо остаётся 300k (скейл opt-in).
- **Greengage-специфика**: проверено на Greenplum 6.25 (форк-совместимо); прогон на самом Greengage не делался.
- **eval (3.5)**: golden-кейсы на GP-демо не добавлялись (инвариант 8 — нужен live GraceKelly; делать отдельно).
- **DataLens (3.1/3.2)**: блокировано (нужен IAM/workbook+HC) — см. `2026-06-13-phase3-prep.md`.
- ~~**multi-level/list-партиции**~~ ✅ 2026-06-13: интроспектор читает все уровни (`partition_key='date, region'`),
  live-валидировано + unit-тест (см. секцию выше).
- **REPLICATED dims на скейле, motion на large-large join** — не покрыты на demo.
