# T-64: одинаковый отказ для model.yaml без mapping

Цель: `verify` для base/candidate и `capture` отвергают документ без mapping
до gate, запроса движку и записи output; явный `{}` остаётся допустимым.
Срез выбран synthetic Astra xhigh по ADR 0006, не human acceptance.

- [x] Подтвердить причину: `NativeModelSource.snapshot` подменяет ложные значения на `{}`.
- [x] Сохранить контрольные snapshots/bundles допустимых mapping до исправления.
- [x] Luna: расширить регрессии и подтвердить red, затем убрать подмену и получить green.
- [x] Обновить контракт и краткие записи состояния, сохранив unrelated untracked files.
- [x] Проверить diff и результаты независимо, сравнить bundles, получить Astra review.
- [x] Выполнить целевые pytest, Ruff, Black и strict mypy перед scoped local commit.

Scope: `auto_bi/semantic/gateway.py`, два тестовых модуля, существующие документы
контракта/архитектуры/состояния. v1 `SemanticModel.load` и asset loader не меняются.
Версии verifier 97 / check 72 / native source 3 остаются при неизменности
snapshots/bundles допустимых входов. Стенды и внешние действия не требуются.

Проверено 2026-10-08 на Windows, Python 3.13.7:

- Регрессии Luna: 40 failed / 1 passed до исправления → 41 passed после него.
  Десять документов проверены напрямую в source и в base/candidate/capture;
  query/probe guards, CLI exit 2, однострочная ошибка, отсутствие output.
- Независимый прогон Codex: 553 passed, 1 skipped, exit 0
  (существующий symlink-тест требует недоступных на Windows прав):
  `uv run pytest tests/test_unreadable_run_inputs.py tests/test_semantic_gateway.py tests/test_semantic_diff.py tests/test_decision_verify.py tests/test_decision_cli.py tests/test_result_capture.py tests/test_unbuilt_contract_yaml.py tests/test_run_input_schema_refusals.py -q --tb=short`.
- `uv run ruff check auto_bi/semantic/gateway.py tests/test_semantic_gateway.py tests/test_unreadable_run_inputs.py`
  и `uv run black --check` на тех же файлах: exit 0.
- `uv run --with mypy --with types-PyYAML mypy --strict auto_bi`: exit 0, 122 файла.
- До/после на normal mapping и `{}` в каждом ref: совпали snapshots, verdict и
  все 44 файла трёх bundles. Локальные логи и сравнение — `.tmp/t64/` (не tracked).
- Synthetic Astra xhigh: выбран контракт и выполнено финальное review, 0 P1/P2.
  Это не human/production acceptance. Полный offline suite и live-стенды не запускались.
