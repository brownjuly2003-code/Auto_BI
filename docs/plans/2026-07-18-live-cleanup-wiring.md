# Live-cleanup wiring: auto-prune on rebuild + `auto_bi prune` (2026-07-18)

## Goal

Продукт сам вызывает ownership-based чистку сирот (P0-2 crit.4, живьём доказана 18.07):
rebuild сессии удаляет артефакты своих ПРОШЛЫХ сборок; `auto_bi prune` чистит их же
по всему леджеру. Дизайн утверждён Юлей 18.07 («авто-прунинг + команда», авто-часть с
kill-switch).

## Design decisions

- `Settings.prune_on_rebuild: bool = True` — kill-switch `AUTO_BI_PRUNE_ON_REBUILD=false`.
  Default ON: это выбранное продуктовое поведение (rebuild = замена), селекция
  session+owner-scoped и shared-kinds исключены в SQL по умолчанию.
- `delete_artifact(kind, native_id)` — опциональный concrete-хелпер адаптеров
  (как `set_artifact_namespace`/`drain_build_artifacts`), BIAdapter Protocol НЕ меняется (S4
  не срабатывает). Возврат без исключения = удалён ИЛИ уже отсутствовал (404); исключение =
  оставить строку live. `database`/неизвестный kind → ValueError (второй пояс защиты после
  SQL-исключения SHARED_BI_KINDS).
- Порядок удаления: chart → dashboard → dataset (живой прогон 18.07).
- Прунинг никогда не валит сборку: обёртка try/except на весь шаг + per-row; дашборд уже
  отдан пользователю.
- `Store.stale_bi_artifacts(session_id=None)` — live-строки сборок, НЕ являющихся последней
  сборкой своей сессии. Последний дашборд каждой сессии всегда остаётся (prune удаляет
  ревизии, не чужие дашборды).
- DataLens scope map: dataset→`dataset`, chart→`widget`, dashboard→`dash`
  (`mix/deleteEntry {entryId, scope}`); имена канонические с fingerprint build-namespace →
  прошлые ревизии реально живы и удаляются по id.

## Tasks

- [x] 1. Store: `stale_bi_artifacts` (+ include_shared=False по умолчанию) → tests/test_store.py
- [x] 2. SupersetClient: `delete()` + `status_code` на `SupersetAPIError`; SupersetAdapter.`delete_artifact` (404 tolerant, database refused) → тесты адаптера
- [x] 3. DataLensClient: `status_code` на `DataLensAPIError`; DataLensAdapter.`delete_artifact` (404 tolerant, database refused) → тесты адаптера
- [x] 4. pipeline: `_prune_superseded_artifacts` + параметр `prune_orphans` в `compile_and_build`/`build_dashboard`; wiring `settings.prune_on_rebuild` во все call-sites (cli `_build`/`_build_raw`/`_build_auto`/chat-approve, serve builder) → tests/test_pipeline.py (rebuild прунит; ошибка delete не валит билд; адаптер без хелпера = no-op; флаг off = no-op)
- [x] 5. CLI `auto_bi prune [--session] [--dry-run] [--model]` поверх `stale_bi_artifacts` + общий движок удаления → тест CLI
- [x] 6. Docs: ARCHITECTURE §3.17 (wired), CHANGELOG §Unreleased, USER_GUIDE (prune + флаг), .env.example, DEPLOYMENT (если касается)
- [x] 7. Гейты: pytest весь сьют, ruff/black/mypy — чисто
- [ ] 8. Live verify (Mac-стенд, Superset+CH): build → rebuild той же сессии → прошлая ревизия удалена автоматически (re-GET 404), текущая жива (200), ledger superseded; `auto_bi prune --dry-run` пуст; стенд возвращён как был
- [ ] 9. PR → CI 3/3 → merge → CI main success

## Done when

- [ ] Rebuild сессии не оставляет сирот в Superset (живьём), DataLens покрыт юнитами
- [ ] `auto_bi prune --dry-run`/real работают против леджера
- [ ] Все гейты зелёные, доки синхронны
