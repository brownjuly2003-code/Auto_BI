# DecisionOps — Astra review live R3 и R4

Дата: 2026-09-12. Модель: `gpt-6-astra`, reasoning `xhigh`.
Основание: [ADR 0006](adr/0006-modelled-review-development-gate.md).
Это synthetic owner/design-partner review, не наблюдение человека, измерение времени
или production acceptance.

## Evidence и вывод

Проверены [live R3 package](pilot/2026-09-12-owner-review-live-r3/README.md), его
summary, asset SQL, cost artifact и соответствующие контракты реализации.
Независимый CLI inspection с pinned public key и ожидаемым manifest digest прошёл:
exit `0`, `signature verified`, `0 problem(s)`. Release verdict пакета остаётся `block`.

Технический R3 достаточен для продолжения разработки. Owner action называет запрет
выпуска, причину timezone change, владельца и конкретный чарт. `chart.opaque_sql`
назван отдельно. R4 требует объяснить границы гарантий внутри самого summary.

| Finding | Evidence | Следствие |
|---|---|---|
| P2: provenance можно прочесть как доказательство исполнения | `provenance.established` в R3 summary; архитектура §8.4.1 | Пояснить matching supplied attestation и отсутствие независимого execution proof. |
| P2: cost и capture относятся к разным SQL | `checks/check-dwh.cost.json`: `GROUP BY date, store_id`; reviewed asset: 13-недельный фильтр, `GROUP BY region` | Cost limits compiled reference SQL не доказывают limits captured query. |
| P3: предел подписи объясняется вне summary | R3 README и порядок report → seal → verify в `release_gate.py` | Дать условное объяснение подписи без утверждения, что текущий bundle уже проверен. |

## Выбранный срез

Добавить детерминированный раздел `How to read this evidence` в owner-summary:
supplied result reconciliation, attestation matching, compiled-reference cost scope,
условный смысл trusted signature. Не менять grading, owner action или claims.
Не добавлять signature status внутрь sealed report. Исторические bundles не менять.
Изменение bytes требует `VERIFIER_VERSION=15`; `CHECK_VERSION=9` сохраняется.

Реализация — Luna xhigh; независимые проверки и финальное Astra-review обязательны.
Acceptance: совпавшее число не отменяет block; unattested/missing/skipped outcomes не
получают положительных гарантий; неизвестные measurements не изобретаются; подпись не
становится SQL execution proof или production sign-off; byte determinism сохраняется.

Финальное review потребовало два уточнения: environment matching выполняется только
при объявленной среде run; отсутствие числового counter не отменяет подтверждение
предела успешным выполнением под engine-enforced limits. Это уточнения пояснений,
не изменение grading или execution contract.

Оба уточнения реализованы; повторное targeted Astra xhigh review: **Approve**,
материальных блокеров нет. Независимый прогон
`uv run pytest tests/test_gate_summary.py tests/test_decision_verify.py tests/test_evidence_signing.py -q`
дал `102 passed`, exit `0`. Документация проверяется отдельным
`uv run pytest tests/test_docs_as_code.py -q`; scoped Ruff и Black также обязательны.

Никакой новый platform runtime, trusted capture boundary или внешний action этим
заключением не разрешается. Human evidence остаётся неизмеренным.
