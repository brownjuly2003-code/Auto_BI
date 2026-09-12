# DecisionOps — synthetic owner review 2026-09-12

**Proxy verdict:** scope и действие владельца читаются из свежих v14 отчётов; единый live
owner-пакет для R3 теперь сохранён и проверяем. Human/design-partner outcome не измерен,
Task 8 human outcome и hard gate остаются открытыми; production acceptance не заявляется.

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

Технический follow-up R3 выполнен на HEAD `4f7d165` в разрешённом Mac-контуре с нативным
ClickHouse 24.8.14.39. Его входы, attested result, signed evidence и public trust root лежат
в [live owner-review package](pilot/2026-09-12-owner-review-live-r3/README.md).

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
| Chart impact | Chart overlay и live R3 bundle называют один `computes_metric` chart и отдельный `chart.opaque_sql`; владелец видит и доказанную связь, и границу resolver. |
| Provenance / governance | Live result имеет `query_run` attestation на candidate snapshot и точный digest reviewed SQL. `results.provenance` и reconciliation проходят; reviewed asset всё равно invalidated из-за semantic change, поэтому совпавшее число не отменяет review. |
| Reviewed capture | `decision capture` исполнил reviewed SQL на Mac под `gate_ro` и записал canonical result `msk=0.452054` с environment, executor, query и semantic digests. |
| Runtime cost | ClickHouse plan оценил 91 строку; exact reference SELECT завершился под engine limits 1 000 000 bytes и 5 000 ms. PostgreSQL scanned bytes по-прежнему остаются отдельной честной границей `cost.not_measured`. |
| Ed25519 | Live manifest подписан pilot-only ключом вне checkout; inspection с tracked public SPKI дала `signature_verified=true`. Подпись доказывает producer/неизменность bundle, но не production sign-off, trusted time или человеческое решение. |

Понятность verdict, changed scope и первого owner action в прочитанных v14 summary
проходит proxy-проверку. Разобраться, как устранить provenance gap и какие гарантии даёт
подпись, удалось с документацией; самодостаточность этих шагов для владельца не доказана.

## Findings и вопросы

Severity: P1 — искажает основу решения; P2 — мешает review; P3 — вопрос понятности.

| ID | Severity | Finding / следующий шаг |
|---|---|---|
| R1 | P1 | Current state называет `docs/pilot/` verifier 7, фактические manifest — v2. Четыре tracked пути подтверждены `git ls-files`; v7 run ID есть в тексте прежнего review, но не в tracked evidence. Развести исторические наборы и дать проверяемое расположение v7, прежде чем ссылаться на него в owner gate. Это документационное расхождение, не отказ integrity checker. |
| R2 | P2 | Pilot не показывает chart, вычисляющий governed-маржу: владелец получает affected dashboard и ноль charts. Это пробел демонстрации, а не установленный дефект resolver. Для реального review нужен соответствующий чарт и отдельный unresolved пример. |
| R3 | P2 | Техническая часть закрыта tracked live-пакетом: reviewed SQL → capture → governance → runtime cost → authenticated evidence. Наблюдение за тем, правильно ли владелец связывает гарантии, остаётся частью human gate. |
| R4 | P3 | `computes_metric`, `provenance`, `trust root` и `cost.not_measured` требуют понимания границ. Проверить у владельца, что совпавшее число или signed bundle не принимаются за доказательство исполнения SQL/production sign-off. |

## Follow-up 2026-09-12

- R1 закрыт исправлением current state: четыре исторических tracked bundle обозначены как
  verifier 2, а отсутствующие в Git Mac v7 runs больше не представлены как tracked evidence.
- R2 закрыт как пробел демонстрации отдельным
  [tracked chart-impact overlay](pilot/2026-09-12-owner-review-chart-impact/README.md).
  Свежий verifier 14 report называет ровно один chart `computes_metric` и один unresolved
  `chart.opaque_sql`; bundle содержит 18 artifacts и проходит integrity inspection.
- Техническая часть R3 закрыта
  [live owner-review package](pilot/2026-09-12-owner-review-live-r3/README.md): capture
  аттестует candidate, runtime checks исполнены на Mac, signed bundle проходит pinned-key
  inspection. Его release verdict ожидаемо `block` из-за governed timezone change.
- R4 и наблюдаемая человеком часть R3 остаются открытыми до review реального владельца.
  Live package не меняет human/design-partner gate.

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
| Mac R3 runner: capture → signed verify → trusted inspection | 15870 | 0; inner verify 4, final `DONE` |

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
Два synthetic bundles находятся в ignored `.tmp/`. Отдельный live R3 bundle сохранён в
`docs/pilot/2026-09-12-owner-review-live-r3/` и проверяется так:

```powershell
$live = 'docs/pilot/2026-09-12-owner-review-live-r3'
uv run auto_bi decision evidence "$live/evidence" --trust-key "$live/trust/evidence-public.pem" --expect-digest sha256:87cf811193731d9a8e2cdafdacf1d7e2e9c3c2068bb1a9083b517b6fc7f19519
```

Свежая проверка документации и tracked-ссылок:
`uv run pytest tests/test_docs_as_code.py -q` — `24 passed`, exit `0`.

## Что обязательно для реального owner gate

Назначенный actual/likely user выбирает свой реальный change и сначала получает отчёты
без объяснения ответов. Зафиксировать его слова про verdict, продукт/чарт, owner action,
происхождение числа, непроведённые проверки и предел подписи; затем — затруднения и помощь.
Отдельно измерить stopwatch-время до правильного решения и baseline обычного review,
чтобы оценивать сокращение времени. Реальное elapsed human time здесь **не измерено**;
оценка по скорости чтения также не подставлялась. Нужны design-partner feedback и явное
решение владельца. До этого ограничения ADR 0004 сохраняются полностью.
