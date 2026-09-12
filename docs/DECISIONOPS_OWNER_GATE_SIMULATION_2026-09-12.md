# DecisionOps — synthetic owner review 2026-09-12

**Proxy verdict:** scope и действие владельца читаются из свежих v14 отчётов; пакет для
реального owner review требует уточнений ниже. Human/design-partner outcome не измерен,
Task 8 и hard gate остаются открытыми; production acceptance не заявляется.

## Сценарий и метод

Роль модели — decision owner `commercial-analytics`: решить, выпускать ли изменение
timezone `Europe/Moscow → UTC` у `metric://sales/gross_margin_pct`, и не приписать его
контрольному `weekly_revenue`. Это desk walkthrough с доступом к документации и тестам,
а не blind test реального пользователя. Историческое [ревью 04.09](DECISIONOPS_OWNER_GATE_SIMULATION_2026-09-04.md)
не переиспользуется как доказательство новых возможностей.

Проверяемый код: HEAD `d2f7c59`, verifier `14`, check `9`. Windows, offline,
без DWH/BI подключений. Источники: [current state](DECISIONOPS_CURRENT_STATE.md),
[архитектура](DECISIONOPS_ARCHITECTURE.md), [pilot](DECISIONOPS_PILOT_2026-08-25.md),
[ADR 0004](adr/0004-synthetic-proxy-offline-investigation.md),
[ADR 0005](adr/0005-ed25519-evidence-trust-root.md).

## Walkthrough и ответы simulated owner

1. Открыты tracked affected/control отчёты через `decision evidence --artifact
   report-summary-md`. Оба чтения завершились exit `0`: это integrity verdict,
   хотя внутри affected стоит `block`. Все четыре tracked manifest содержат verifier
   **2**, `content_hashed`, по 19 artifacts. Заявленные в предыдущем review Mac v7
   `run-7d96f81f2ac4009f` и `run-fae00f0db7e5a6ff` отсутствуют среди tracked manifest.
2. `decision validate` подтвердил 2 продукта, 2 metric contracts, 0 issues.
3. На неизменённых tracked входах созданы отдельные v14 affected/control bundles.
   Оба содержат 15 artifacts и прошли self-verification с 0 problems. Прочитаны их
   `reports/summary.md`, включая `Owner action`, impact, findings и непроведённые проверки.
4. **Affected:** не выпускать candidate; timezone меняет governed-маржу, затронуты
   `dashboard://weekly_margin@7` и `commercial-analytics`; asset требует повторного review.
   `result.equivalent` говорит о совпадении поданного числа, но `provenance.unattested`
   прямо сообщает, что запрос, среда и semantics его получения не записаны.
   Действие: разрешить blocking change, обновить reviewed asset и получить attested result.
5. **Control:** `needs_review`, `impact.none`, 0 из 1 изменений достигают продукта.
   Отчёт явно говорит, что review не означает изменения чисел этого продукта. Числа
   выручки не сверялись (`result.no_expectations`); все пять DWH checks не настроены.
   Действие: владелец рассматривает semantic finding и эти ограничения перед выпуском.

## Новые возможности: что удалось оценить

| Возможность | Наблюдение и граница evidence |
|---|---|
| Chart impact | Свежий affected называет dashboard, но 0 charts и 0 unresolved. В tracked `weekly_margin@7.yaml` единственный чарт `revenue_by_region` считает SUM(revenue), а не ratio маржи. Положительный `computes_metric` и `chart.opaque_sql` представлены в `tests/test_decision_verify.py`; этот pilot их не демонстрирует. |
| Provenance / governance | `unattested` виден рядом с успешным reconciliation. У pilot asset нет `reference_query`, поэтому отсутствие `query_ungoverned` не доказывает governance SQL. В текущем контракте reviewed SQL сверяется со snapshot по таблицам/колонкам; matching attestation сама этого не заменяет. |
| Reviewed capture | CLI help показывает asset selection, candidate, environment и новый output. Прочитаны контрактные тесты exact SQL, canonical YAML, nullable/schema и отказа от overwrite до подключения. Для pilot сначала нужен reviewed SQL; живой capture в этом review не выполнялся. |
| Runtime cost | Свежий offline отчёт честно показывает `check.not_configured`. По архитектуре exact reference SELECT метрики имеет CH byte/timeout limits и PG statement timeout; это отдельный запрос от asset capture. `max_scanned_rows` остаётся оценкой плана, PG scanned bytes — `cost.not_measured` (review). Живые counters здесь не измерены. |
| Ed25519 | ADR и CLI tests различают content hashes и pinned public trust root: signed inspection без ключа — block/4, выдача artifact без trust root — usage error/2; правильный ключ разрешает чтение. Подпись доказывает producer/неизменность bundle, но не SQL execution и не trusted time. Наши CLI bundles unsigned; operational key не создавался. |

Понятность verdict, changed scope и первого owner action в прочитанных v14 summary
проходит proxy-проверку. Разобраться, как устранить provenance gap и какие гарантии даёт
подпись, удалось с документацией; самодостаточность этих шагов для владельца не доказана.

## Findings и вопросы

Severity: P1 — искажает основу решения; P2 — мешает review; P3 — вопрос понятности.

| ID | Severity | Finding / следующий шаг |
|---|---|---|
| R1 | P1 | Current state называет `docs/pilot/` verifier 7, фактические manifest — v2. Четыре tracked пути подтверждены `git ls-files`; v7 run ID есть в тексте прежнего review, но не в tracked evidence. Развести исторические наборы и дать проверяемое расположение v7, прежде чем ссылаться на него в owner gate. Это документационное расхождение, не отказ integrity checker. |
| R2 | P2 | Pilot не показывает chart, вычисляющий governed-маржу: владелец получает affected dashboard и ноль charts. Это пробел демонстрации, а не установленный дефект resolver. Для реального review нужен соответствующий чарт и отдельный unresolved пример. |
| R3 | P2 | Нет единого показанного owner-пакета с reviewed SQL → capture → governance → runtime cost → authenticated evidence. Наличие отдельных контрактов не доказывает, что owner правильно свяжет эти гарантии. Подготовить такой пример в разрешённом контуре и наблюдать его прочтение человеком. |
| R4 | P3 | `computes_metric`, `provenance`, `trust root` и `cost.not_measured` требуют понимания границ. Проверить у владельца, что совпавшее число или signed bundle не принимаются за доказательство исполнения SQL/production sign-off. |

## Команды и машинное время

Однократный замер PowerShell `Stopwatch` вокруг `uv run`, включая запуск CLI и его IO,
без ожидания/чтения моделью. Это не benchmark и не человеческое time-to-decision.

| Команда / сценарий | Время, ms | Exit / наблюдение |
|---|---:|---|
| `decision capture --help` | 700 | 0 |
| `decision evidence …ch-tz-margin/evidence --artifact report-summary-md` | 2770 | 0; исторический block |
| `decision evidence …ch-tz-revenue/evidence --artifact report-summary-md` | 2511 | 0; исторический needs_review |
| `decision validate …ch-tz-margin/contracts` | 1128 | 0; 0 issues |
| `decision verify`, affected v14 | 2934 | 4; block |
| `decision verify`, control v14 | 1715 | 3; needs_review |

Воспроизведение из корня репозитория, PowerShell; каждому запуску нужен свой новый
evidence-каталог. Все строки `decision …` выше имеют префикс `uv run auto_bi`.

```powershell
$p = 'docs/pilot/2026-08-25-ch-tz-margin'
uv run auto_bi decision validate "$p/contracts"
uv run auto_bi decision evidence "$p/evidence" --artifact report-summary-md
uv run auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-revenue/evidence --artifact report-summary-md
uv run auto_bi decision capture --help
uv run auto_bi decision verify "$p/contracts" --base "$p/base" --candidate "$p/candidate" --dashboards "$p/dashboards" --assets "$p/assets" --results "$p/results.yaml" --product decision://sales/weekly_margin --environment synthetic-offline --created-at 2026-09-12T12:00:00Z --evidence .tmp/owner-review-2026-09-12-astra/affected
uv run auto_bi decision verify "$p/contracts" --base "$p/base" --candidate "$p/candidate" --dashboards "$p/dashboards" --assets "$p/assets" --results "$p/results.yaml" --product decision://sales/weekly_revenue --environment synthetic-offline --created-at 2026-09-12T12:00:00Z --evidence .tmp/owner-review-2026-09-12-astra/control
```

Affected: `run-a7c5f8da2860617b`, digest
`sha256:65e4a6de720e1d085fbc21c7c9e16844b45b1202abb2a46d4b5f89fb46b6c5d4`.
Control: `run-65ea807cc9489802`, digest
`sha256:fd066cf114d745c45e4bda7c05f40b89f2b5f72c0985595a773a86497fb25c0a`.
Новые bundles находятся в ignored `.tmp/`; tracked pilot не изменялся.

Запущен один offline pytest по `test_decision_verify`, `test_decision_chart_impact`,
`test_result_provenance`, `test_result_capture`, `test_result_probe`,
`test_environment_checks`, `test_runtime_probe`, `test_evidence_signing`,
`test_decision_cli` (`tests/<name>.py -q`). На момент записи завершение не подтверждено:
PowerShell-команды задержались без результата; свежий pass этих тестов не заявляется.

## Что обязательно для реального owner gate

Назначенный actual/likely user выбирает свой реальный change и сначала получает отчёты
без объяснения ответов. Зафиксировать его слова про verdict, продукт/чарт, owner action,
происхождение числа, непроведённые проверки и предел подписи; затем — затруднения и помощь.
Отдельно измерить stopwatch-время до правильного решения и baseline обычного review,
чтобы оценивать сокращение времени. Реальное elapsed human time здесь **не измерено**;
оценка по скорости чтения также не подставлялась. Нужны design-partner feedback и явное
решение владельца. До этого ограничения ADR 0004 сохраняются полностью.
