"""CLI `prune` dispatch: ledger-wide cleanup of prior-revision BI artifacts.

The selection logic is covered by test_store.py and the deletion engine by
test_pipeline.py; this locks the CLI wiring — stale rows found via a REAL Store,
fed to the (faked) adapter's delete_artifact, superseded on success, with dry-run
and unhealthy-BI short-circuits.
"""

import auto_bi.adapters.factory as factory
import auto_bi.config as config
from auto_bi.adapters.base import AdapterHealth
from auto_bi.cli import main
from auto_bi.config import Settings
from auto_bi.store import Store

MODEL = "semantic/model.yaml"  # the committed demo model (repo root is pytest cwd)


class _FakeAdapter:
    def __init__(self, healthy: bool = True, fail_ids: set[str] | None = None) -> None:
        self.healthy = healthy
        self.fail_ids = fail_ids or set()
        self.deleted: list[tuple[str, str]] = []

    def healthcheck(self) -> AdapterHealth:
        return AdapterHealth(ok=self.healthy, message="" if self.healthy else "down")

    def delete_artifact(self, kind: str, native_id: str) -> None:
        if native_id in self.fail_ids:
            raise RuntimeError("boom")
        self.deleted.append((kind, native_id))


def _seed_two_builds(store: Store) -> str:
    """One session, two successful builds; build t1 is the stale revision."""
    session = store.create_session("прунинг")
    for token, base in (("t1", 100), ("t2", 200)):
        for kind, native_id in (
            ("database", "1"),  # shared: never a delete candidate
            ("dataset", str(base + 1)),
            ("chart", str(base + 2)),
            ("dashboard", str(base + 3)),
        ):
            store.record_bi_artifact(
                session_id=session,
                build_token=token,
                target_bi="superset",
                kind=kind,
                native_id=native_id,
                name=f"{kind}-{native_id}",
            )
    return session


def _wire(monkeypatch, tmp_path, adapter) -> Store:
    store = Store(str(tmp_path / "prune.sqlite"))
    settings = Settings(store_path=str(tmp_path / "prune.sqlite"))
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(factory, "make_adapter", lambda *a, **k: adapter)
    return store


def _statuses(store: Store, session: str) -> dict[str, str]:
    return {f"{r['kind']}:{r['native_id']}": r["status"] for r in store.bi_artifacts(session)}


def test_prune_empty_ledger(monkeypatch, tmp_path, capsys) -> None:
    adapter = _FakeAdapter()
    _wire(monkeypatch, tmp_path, adapter)
    rc = main(["prune", "--model-path", MODEL])
    assert rc == 0
    assert "Сирот прошлых ревизий нет" in capsys.readouterr().out
    assert adapter.deleted == []


def test_prune_dry_run_deletes_nothing(monkeypatch, tmp_path, capsys) -> None:
    adapter = _FakeAdapter()
    store = _wire(monkeypatch, tmp_path, adapter)
    session = _seed_two_builds(store)
    rc = main(["prune", "--dry-run", "--model-path", MODEL])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Dry-run" in out and "Кандидаты" in out
    assert adapter.deleted == []
    assert all(status == "live" for status in _statuses(store, session).values())


def test_prune_deletes_stale_in_safe_order_and_supersedes(monkeypatch, tmp_path) -> None:
    adapter = _FakeAdapter()
    store = _wire(monkeypatch, tmp_path, adapter)
    session = _seed_two_builds(store)
    rc = main(["prune", "--model-path", MODEL])
    assert rc == 0
    # only build t1's non-shared rows, in the live-proven order chart -> dashboard -> dataset
    assert adapter.deleted == [("chart", "102"), ("dashboard", "103"), ("dataset", "101")]
    statuses = _statuses(store, session)
    assert statuses["chart:102"] == "superseded"
    assert statuses["dashboard:103"] == "superseded"
    assert statuses["dataset:101"] == "superseded"
    # shared connection and the latest build stay live
    assert statuses["database:1"] == "live"
    assert statuses["chart:202"] == "live"
    assert statuses["dashboard:203"] == "live"


def test_prune_failed_delete_keeps_row_live_and_exits_1(monkeypatch, tmp_path) -> None:
    adapter = _FakeAdapter(fail_ids={"103"})
    store = _wire(monkeypatch, tmp_path, adapter)
    session = _seed_two_builds(store)
    rc = main(["prune", "--model-path", MODEL])
    assert rc == 1
    statuses = _statuses(store, session)
    assert statuses["dashboard:103"] == "live"  # retried by a later prune
    assert statuses["chart:102"] == "superseded"
    assert statuses["dataset:101"] == "superseded"


def test_prune_unknown_target_rows_skipped_not_traceback(monkeypatch, tmp_path, capsys) -> None:
    """A ledger row with an unknown target_bi is reported as skipped; valid targets still run."""
    adapter = _FakeAdapter()
    store = _wire(monkeypatch, tmp_path, adapter)
    session = _seed_two_builds(store)  # superset: t1 stale, t2 latest
    ghost = store.create_session("неизвестный таргет")
    for token, native_id in (("g1", "901"), ("g2", "902")):
        store.record_bi_artifact(
            session_id=ghost,
            build_token=token,
            target_bi="mssql",
            kind="dashboard",
            native_id=native_id,
            name=f"dashboard-{native_id}",
        )
    rc = main(["prune", "--model-path", MODEL])
    assert rc == 1  # skipped rows -> non-zero, but no traceback
    out = capsys.readouterr().out
    assert "mssql" in out and "пропущено" in out
    # the unknown-target stale row is untouched, the superset revision is still pruned
    assert _statuses(store, ghost)["dashboard:901"] == "live"
    assert _statuses(store, session)["chart:102"] == "superseded"


def test_prune_unhealthy_bi_skips_target(monkeypatch, tmp_path, capsys) -> None:
    adapter = _FakeAdapter(healthy=False)
    store = _wire(monkeypatch, tmp_path, adapter)
    session = _seed_two_builds(store)
    rc = main(["prune", "--model-path", MODEL])
    assert rc == 1
    assert "недоступен" in capsys.readouterr().out
    assert adapter.deleted == []
    assert all(status == "live" for status in _statuses(store, session).values())
