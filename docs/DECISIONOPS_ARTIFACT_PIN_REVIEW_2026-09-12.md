# DecisionOps — manifest pin при чтении artifact

Дата: 2026-09-12. Synthetic development review: `gpt-6-astra`, reasoning `xhigh`,
по [ADR 0006](adr/0006-modelled-review-development-gate.md).
Это не human acceptance или production certification.

## Дефект и выбранный срез

P1: `decision evidence --artifact ID --expect-digest DIGEST` игнорирует manifest pin.
CLI возвращает payload из ранней ветки `--artifact`, не передавая expected digest
читателю. Воспроизведение на tracked historical bundle:

```powershell
uv run auto_bi decision evidence docs/pilot/2026-08-25-ch-tz-margin/evidence --artifact report-summary-md --expect-digest sha256:0000000000000000000000000000000000000000000000000000000000000000
```

До исправления: exit `0`, stdout содержит report, хотя digest заведомо не совпадает.

Astra выбрала ограниченный срез: передавать pin в `read_artifact`, сравнивать digest
исходных manifest bytes и разбирать именно проверенные bytes. Mismatch должен давать
exit `4`, stderr с `evidence.digest_unexpected` и пустой stdout.

Правильный pin сохраняет существующие проверки подписи, classification и digest
выбранного payload. Повреждённый посторонний artifact не расширяет scope чтения.
Полная inspection всего bundle остаётся отдельным режимом. `VERIFIER_VERSION=15`
и `CHECK_VERSION=9` сохраняются: формат создаваемого evidence и grading не меняются.

Реализация — Luna xhigh, сначала red regression. Acceptance включает неправильный и
правильный pin, signed bundle с trusted key, отказы без доверенного ключа, restricted
и повреждённый выбранный payload, а также совместимость чтения без pin.
Независимая проверка повторяет исходную CLI-команду и профильные pytest; финальное
содержательное review выполняет Astra xhigh.

## Результат

Реализовано Luna xhigh. Финальное Astra xhigh review: **Approve**, материальных
замечаний нет. Независимые проверки:

- `uv run pytest tests/test_decision_cli.py tests/test_evidence_inspect.py tests/test_evidence_signing.py -q`
  — `62 passed`, exit `0`.
- Исходная команда с нулевым digest — exit `4`, `evidence.digest_unexpected`;
  regression test подтверждает пустой stdout.
- Та же команда с digest tracked manifest — exit `0`; stdout совпадает с выбранным report.

Pre-implementation evidence: родитель воспроизвёл поведенческий дефект исходной
CLI-командой выше. У Luna зафиксирован только collection failure нового теста
(`ImportError` отсутствующего `EvidenceDigestError`), а не assertion `0 != 4`;
отдельный поведенческий red pytest не заявляется.

Проверка wrong-key + correct-pin отдельно не добавлялась: Astra оценила сохранённый
обязательный signature check и существующее негативное покрытие как достаточные.
