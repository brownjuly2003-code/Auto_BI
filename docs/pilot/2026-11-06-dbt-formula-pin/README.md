# dbt formula pin — 2026-11-06

Pilot среза S4 [ADR 0008](../../adr/0008-dbt-semantic-manifest-external-formulas.md): формулу
`metric://sales/gross_margin_pct@3` определяет dbt — ratio `gross_margin_pct` из
`semantic_manifest.json`. Контракт пинует замыкание этой метрики, а gate читает артефакт каждой
стороны. Pilot показывает три вещи: изменение определения в dbt, которое контракт перепиновал;
то же изменение без перепина; контроль без изменений.

## Входы

- `dbt/base/semantic_manifest.json` — `dbt parse` legacy-проекта S0 (dbt-core 1.12.6,
  dbt-postgres 1.11.0, metricflow 0.213.0, без БД). Байт в байт совпадает с
  `tests/fixtures/dbt_semantic_manifest/legacy/semantic_manifest.json`
  (`sha256:4485a75949c08f10aedae2e6998f1aec9e6a8a17f82f7f4ffbd6fd50c3acee81`).
- `dbt/candidate/semantic_manifest.json` — `dbt parse` того же проекта после одной правки:
  в витрину `sales_daily` добавлена колонка `net_revenue`, и measure `revenue` теперь читает её.
  Артефакты отличаются только полем `$.semantic_models[0].measures[0].expr`.

  ```diff
       measures:
         - name: revenue
           agg: sum
  -        expr: revenue
  +        expr: net_revenue
  ```

- `contracts/` — продукт `weekly_margin` (копия из `2026-08-25-ch-tz-margin`) и контракт
  `gross_margin_pct@3`: формула `external_ref` с пином замыкания base.
- `base/` — витрина `dm.sales_daily` на PostgreSQL с тем же контрактом.
- `candidate-repinned/` — витрина с `net_revenue`; контракт перепинован на замыкание candidate.
- `candidate-drift/` — та же витрина, но в контракте остался старый пин.
- `dashboards/` — `weekly_margin@7` из `2026-08-25-ch-tz-margin`.

Semantic model артефакта называет relation `x.dm.sales_daily`. Поэтому база dbt обеих сторон —
`x`: опции `--base-dbt-database x` и `--candidate-dbt-database x`.

Пин печатает `decision pin`:

```bash
uv run auto_bi decision pin docs/pilot/2026-11-06-dbt-formula-pin/dbt/candidate/semantic_manifest.json gross_margin_pct
# sha256:e13a91d635e199637759b448c2db2f59a602d74a8fc3127197e243d5d9d9a53c  gross_margin_pct
```

Пин base — `sha256:318854cc18766c041ff79d50c440841402e225ef54bd64521e6178be2e29b2db`.

## Прогоны

Все три прогона: verifier 132, check 83, `created_at=2026-11-06T12:00:00Z`, окружение
`synthetic-offline`, `content_hashed`, по 17 артефактов в каждом. Каждый прогон лежит в
`evidence/<случай>/`. Записаны verifier 129 (S4) и перезаписаны verifier 130 (S7),
131 (S7b) и 132 (S8, 2026-11-08): каждый раз изменились только `run_id`,
`verifier_version` и `reports/summary.md`, который их называет; прежние bundles —
`git show <коммит>:<путь>` (`e7df7b9` — verifier 129, `287e686` — 130, `5f8335f` — 131).

| Случай | Кандидат | Run | Evidence digest | Вердикт |
|---|---|---|---|---|
| `repinned` | `candidate-repinned` + `dbt/candidate` | `run-4e3531fa58e778c7` | `sha256:88a6dd341213be4d73d17e8e6318d150275625bc476eafeda506275a388bafaa` | `block`, exit 4 |
| `drift` | `candidate-drift` + `dbt/candidate` | `run-545528fe67dfee35` | `sha256:2d1419a02d89e69cc7c9d8308bb2f8f25d4857d7290c852fa882e250341fe1bb` | `block`, exit 4 |
| `control` | `base` + `dbt/base` | `run-632146bbb8c8eaef` | `sha256:78b284714549deda5fe4be8380c4bcab9c3e1ecf86ee5ca560854ec157b5b535` | `pass_with_advisory`, exit 0 |

- **repinned.** Diff называет review `metric.formula_changed`: типизированная формула,
  разрешённая из артефакта, сменила знаменатель с `sum(revenue)` на `sum(net_revenue)`. Рядом —
  review `metric.binding_changed` (пин и замыкание `318854cc…` → `e13a91d6…`) и advisory
  `field.added` для `net_revenue`. Оба изменения метрики доходят до governed-продукта, поэтому
  `impact.products` поднимает их до block — как любое изменение формулы метрики такого продукта.
  `contracts.validate` — pass: перепин и есть изменение под ревью.
- **drift.** dbt изменился, а контракт не перепинован. Snapshot кандидата несёт block
  `gap.external_revision_mismatch` с обоими digest; формула остаётся `external_ref` с review
  `gap.opaque_metric_formula`. Блокируют `semantic.gaps`, `semantic.diff` и `impact.products`.
- **control.** Обе стороны с одним артефактом: `semantic.unchanged`, `impact.none`. Разрешённая
  формула компилируется в reference SQL (`sql.compiled`). Advisory остаются только потому, что
  нет живого DWH и результатов.

Рядом со `snapshot-base` и `snapshot-candidate` каждый bundle хранит каноническое замыкание
`gross_margin_pct` для своей стороны: `closure-base-gross-margin-pct-03a2ae0f…` и
`closure-candidate-gross-margin-pct-03a2ae0f…`, kind `formula_closure`. Digest каждого
замыкания равен `closure_digest` в binding snapshot той же стороны. `decision evidence` это
проверяет (коды `evidence.closure_*`). Без артефакта кандидата verify останавливается до
gate с exit 2:

```text
error: semantic ref docs/pilot/2026-11-06-dbt-formula-pin/candidate-repinned takes the formula of metric://sales/gross_margin_pct from a dbt semantic manifest; give the artifact with --candidate-semantic-manifest
```

## Проверка и воспроизведение

Проверка сохранённого bundle из корня репозитория:

```bash
uv run auto_bi decision evidence docs/pilot/2026-11-06-dbt-formula-pin/evidence/repinned \
  --expect-digest sha256:88a6dd341213be4d73d17e8e6318d150275625bc476eafeda506275a388bafaa
```

Воспроизведение в новом временном каталоге. Для `drift` в `--candidate` подставляется
`candidate-drift`; для `control` — `--candidate "$P/base"` и `dbt/base` как артефакт кандидата:

```bash
P=docs/pilot/2026-11-06-dbt-formula-pin
uv run auto_bi decision verify "$P/contracts" --base "$P/base" --candidate "$P/candidate-repinned" \
  --base-semantic-manifest "$P/dbt/base/semantic_manifest.json" --base-dbt-database x \
  --candidate-semantic-manifest "$P/dbt/candidate/semantic_manifest.json" --candidate-dbt-database x \
  --dashboards "$P/dashboards" --environment synthetic-offline \
  --created-at 2026-11-06T12:00:00Z --evidence "$(mktemp -d)"
```

`tests/test_dbt_formula_evidence.py` повторяет все три прогона и сравнивает каждый файл bundle
с сохранённым байт в байт.

## Чего pilot не показывает

Pilot работает offline. Живой DWH не опрашивался, MetricFlow не запускался, чисел нет. Что
MetricFlow и reference SQL контракта дают одно число, доказывает матрица паритета S2 на Mac, а
не этот pilot. dbt-проект — синтетический проект S0. Bundle не подписаны. Это evidence работы
gate, а не human approval.
