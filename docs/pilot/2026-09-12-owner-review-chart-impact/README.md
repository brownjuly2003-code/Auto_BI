# Owner-review chart-impact overlay — 2026-09-12

Этот tracked offline overlay закрывает демонстрационный пробел R2 из
[`DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md`](../../DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md).
Исторические входы `2026-08-25-ch-tz-margin` не изменены: overlay заменяет только
dashboard spec и повторно использует его contracts, semantic snapshots, asset и recorded result.

В `dashboards/weekly_margin@7.yaml` добавлены два намеренно разных примера:

- `margin_by_region` точно воспроизводит ratio `sum(gross_margin) / sum(revenue)` и
  разрешается как `computes_metric`;
- `operator_table` содержит операторский `raw_sql`, поэтому честно остаётся именованным
  gap `chart.opaque_sql`.

Tracked evidence выпущен verifier 14 с `created_at=2026-09-12T16:00:00Z`:

- run: `run-a7c5f8da2860617b`;
- manifest: `content_hashed`, 18 artifacts;
- digest: `sha256:33c449a25462a742fe2288588b432c5b4c576b0bb3f4bfe694202ff572ca5808`;
- release verdict: `block`, exit `4`;
- integrity verdict: `pass`, exit `0`.

У run тот же идентификатор, что у прежнего affected-прогона: продукт и semantic snapshots
те же. Конкретный bundle однозначно различается evidence digest и своим каталогом.

Проверка сохранённого bundle из корня репозитория:

```powershell
$overlayPath = 'docs/pilot/2026-09-12-owner-review-chart-impact'
uv run auto_bi decision evidence "$overlayPath/evidence" --expect-digest sha256:33c449a25462a742fe2288588b432c5b4c576b0bb3f4bfe694202ff572ca5808
```

Воспроизведение в новом временном каталоге:

```powershell
$sourcePath = 'docs/pilot/2026-08-25-ch-tz-margin'
$overlayPath = 'docs/pilot/2026-09-12-owner-review-chart-impact'
$replayEvidencePath = Join-Path '.tmp' ('owner-review-chart-impact-' + [guid]::NewGuid().ToString('N'))
uv run auto_bi decision verify "$sourcePath/contracts" --base "$sourcePath/base" --candidate "$sourcePath/candidate" --dashboards "$overlayPath/dashboards" --assets "$sourcePath/assets" --results "$sourcePath/results.yaml" --product decision://sales/weekly_margin --environment synthetic-offline --created-at 2026-09-12T16:00:00Z --evidence "$replayEvidencePath"
```

Это evidence chart-resolution, а не human approval: живой DWH не опрашивался, pilot result
остаётся unattested, bundle не подписан, а time-to-decision реального владельца не измерен.
