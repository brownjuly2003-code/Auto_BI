# DecisionOps — согласованность manifest при inspection

Дата: 2026-09-12. Synthetic development review: `gpt-6-astra`, reasoning `xhigh`,
по [ADR 0006](../adr/0006-modelled-review-development-gate.md).
Это не human acceptance или production certification.

## Дефект и выбранный срез

P1: полная inspection может вернуть bundle B под ожидаемым digest A, если manifest
меняется между чтениями. `inspect_bundle` читает исходные bytes, `verify_bundle`
загружает manifest ещё дважды, затем возвращаемая модель читается отдельно.

Родитель воспроизвёл замену manifest на временном filesystem store: после первого
чтения записывается другой валидный manifest с теми же artifacts и другим product.
Исходные evidence в репозитории не изменялись. Результат до исправления:

```text
VERDICT=pass
PIN_MATCHES_ORIGINAL=True
RETURNED_ORIGINAL_PRODUCT=False
MANIFEST_READS=4
```

Astra выбрала ограниченный срез: одни прочитанные manifest bytes для digest, pin,
модели, canonical validation, проверки подписи и адресов artifacts. Полная inspection
продолжает проверять все artifacts; ранее исправленный `read_artifact` не меняется.
Версии verifier/check сохраняются: writer и формат evidence не изменяются.

Это snapshot только manifest в памяти, не атомарный snapshot всего каталога.

Реализация — Luna xhigh. Перед кодом требуется behavioral red regression: замена после
первого чтения не должна возвращать продукт B под digest A; замена после проверки
подписи не должна передавать B результат проверки A. Совместимость покрывает неверный
pin, malformed/noncanonical manifest, неверную подпись и повреждённые artifacts.

## Red evidence

До production edit Luna выполнила:

```text
uv run pytest tests/test_evidence_inspect.py::test_inspection_uses_one_manifest_snapshot_for_digest_and_bundle tests/test_evidence_inspect.py::test_verify_bundle_uses_the_manifest_bytes_it_already_read tests/test_evidence_signing.py::test_inspection_keeps_signed_manifest_snapshot_after_signature_check -q
```

Exit `1`, `3 failed`: unsigned и signed inspection возвращали replacement product B,
а direct `verify_bundle` сообщал `evidence.manifest_not_canonical` из-за смешения bytes.

## Результат

Реализовано Luna xhigh; финальное Astra xhigh review — **Approve**, без материальных
замечаний. Родитель независимо проверил:

- `uv run pytest tests/test_evidence_inspect.py tests/test_evidence_store.py tests/test_evidence_signing.py tests/test_decision_cli.py tests/test_decision_verify.py -q`
  — `153 passed, 1 skipped`, exit `0`. Existing symlink test пропущен из-за отсутствия
  Windows-привилегий на создание symlink; новые regression tests прошли.
- Исходное filesystem-воспроизведение: `pass`, исходный pin и продукт, одно чтение manifest.
- Tracked signed R3 bundle с pinned public key и ожидаемым digest — `signature verified`,
  `0 problem(s)`, exit `0`.
- Scoped Ruff и Black прошли.
