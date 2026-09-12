# Live owner-review package R3 — 2026-09-12

Этот tracked пакет закрывает техническую часть R3 из
[`DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md`](../../DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-12.md):
один пример связывает reviewed SQL, live capture, governance, runtime cost и
аутентифицированный evidence bundle. Он не содержит решения реального владельца и не
закрывает human/design-partner gate или R4.

## Сценарий

Candidate меняет timezone `metric://sales/gross_margin_pct@3` с `Europe/Moscow` на `UTC`.
Изменение достигает governed-продукта `decision://sales/weekly_margin@1` и чарта
`dashboard://weekly_margin@7#margin_by_region`, который воспроизводит формулу метрики.
`operator_table` намеренно остаётся именованным gap `chart.opaque_sql`.

Reviewed asset приколот к base snapshot, как прежнее одобренное ожидание. `decision capture`
исполнил его `reference_query` против candidate и записал в `results.yaml`:

- base semantic digest:
  `sha256:6686e782cde5f3c53f30793052f736c9a41a1722090ee43ef2d480b391f3df7e`;
- candidate semantic digest:
  `sha256:07a6d4053a8c71f8b96f99a17c2713a1b9b62a02192945d28d6133ccae5258a2`;
- reviewed/query digest:
  `sha256:2bb4898389f6c8f85f0d61aeed1074f589fc69f870dc72baf5b49e53737fadd0`;
- captured result: `msk`, `0.452054`; origin `query_run`, executor `gate_ro`;
- environment: `owner-review-mac-native-ch24.8.14.39`.

## Live contour и runtime evidence

Прогон выполнен на Mac нативным ClickHouse 24.8.14.39, без Docker, на изолированных
HTTP/TCP-портах `18123/19000`. Фикстура содержит 91 строку в четырёх партициях. Роль
`gate_ro` использует `readonly=2`: запись запрещена, а query settings разрешены, поэтому
движок может применить `max_bytes_to_read` и `max_execution_time`. Роль получила `SELECT`
на `dm.*` и read-only доступ к `system.*`, нужный verifier для наблюдения grants и row
policies.

`dwh.cost` зафиксировал `EXPLAIN ESTIMATE: 91 row(s) in 4 part(s), 4 mark(s)` и завершение
точного reference SELECT под обоими пределами: `max_scanned_bytes=1000000` и
`timeout_ms=5000`. ClickHouse/driver в этом режиме не вернул числовые `read_bytes` и
`elapsed_ns`; успешное завершение при engine-enforced limits записано как
`checked_bounds=[max_scanned_bytes, timeout_ms]`.

## Evidence

- run: `run-a7e2e9f914a502de`;
- verifier: `14`, created at `2026-09-12T15:38:21Z`;
- release verdict: `block`, exit `4`;
- integrity: `signed`;
- signing key id:
  `sha256:2be2fac9f00508dae835b0bd9d582734873aa93a1ce7ac92b622a044b71c2798`;
- manifest digest:
  `sha256:87cf811193731d9a8e2cdafdacf1d7e2e9c3c2068bb1a9083b517b6fc7f19519`;
- trusted inspection: `pass`, `signature_verified=true`, exit `0`.

`contracts.validate`, `results.reconcile`, `results.provenance`, `sql.compile`,
`dwh.access`, `dwh.physical`, `dwh.security` и `dwh.cost` прошли. Единственный block —
`impact.products`: timezone change достигает governed-продукта. `semantic.diff`,
`assets.invalidated` и `dwh.freshness` требуют review; Date-фикстура на момент прогона была
старше `PT6H`. `bi.contract` остаётся advisory pure compile.

Проверка сохранённого bundle из корня репозитория:

```powershell
$p = 'docs/pilot/2026-09-12-owner-review-live-r3'
uv run auto_bi decision evidence "$p/evidence" `
  --trust-key "$p/trust/evidence-public.pem" `
  --expect-digest sha256:87cf811193731d9a8e2cdafdacf1d7e2e9c3c2068bb1a9083b517b6fc7f19519
```

В Git сохранён только public trust root. Pilot-only private PKCS#8 key создан runner'ом на
Mac вне checkout с mode `0600`; в архив результатов, логи и Windows workspace он не
копировался. Подпись аутентифицирует producer и неизменность bundle, но сама по себе не
является production sign-off и не заменяет наблюдение за решением владельца.
