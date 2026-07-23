"""plan_sol step 12: SQLite online backup integrity + restore drill."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from auto_bi.store import Store

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "store_backup.py"


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "src.sqlite")
    yield s
    s.close()


def test_integrity_ok_on_fresh_store(store: Store) -> None:
    assert store.is_integrity_ok()
    assert store.integrity_check() == ["ok"]


def test_backup_preserves_sessions_and_specs(store: Store, tmp_path: Path) -> None:
    sid = store.create_session("выручка")
    store.add_message(sid, "user", "выручка")
    store.save_spec(sid, {"title": "T", "charts": []}, status="approved")
    store.save_build(sid, None, dashboard_id=42, url="/d/42/", status="ok")

    dest = tmp_path / "backup.sqlite"
    out = store.backup_to(dest)
    assert out == dest
    assert dest.is_file()

    restored = Store(dest)
    try:
        assert restored.is_integrity_ok()
        msgs = restored.messages(sid)
        assert [(m["role"], m["content"]) for m in msgs] == [("user", "выручка")]
        specs = restored.specs(sid)
        assert specs[0]["spec_json"]["title"] == "T"
        builds = restored.builds(sid)
        assert builds[0]["dashboard_id"] == 42
        assert builds[0]["url"] == "/d/42/"
    finally:
        restored.close()


def test_backup_overwrites_existing_dest(store: Store, tmp_path: Path) -> None:
    store.create_session("a")
    dest = tmp_path / "b.sqlite"
    store.backup_to(dest)
    store.create_session("b")
    store.backup_to(dest)
    restored = Store(dest)
    try:
        n = restored._rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
        assert n == 2
    finally:
        restored.close()


def test_restored_store_accepts_new_writes(store: Store, tmp_path: Path) -> None:
    store.create_session("seed")
    dest = tmp_path / "restored.sqlite"
    store.backup_to(dest)
    restored = Store(dest)
    try:
        sid = restored.create_session("after-restore")
        restored.add_message(sid, "agent", "hi")
        assert restored.messages(sid)[0]["content"] == "hi"
        assert restored.is_integrity_ok()
    finally:
        restored.close()


def test_backup_rejects_memory_dest(store: Store) -> None:
    with pytest.raises(ValueError, match="memory"):
        store.backup_to(":memory:")


def _load_script():
    spec = importlib.util.spec_from_file_location("store_backup_script", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_script_backup_check_restore_drill(tmp_path: Path) -> None:
    src = tmp_path / "live.sqlite"
    s = Store(src)
    try:
        s.create_session("script-seed")
        s.add_message(s._rows("SELECT id FROM sessions")[0]["id"], "user", "x")
    finally:
        s.close()

    mod = _load_script()
    dest = tmp_path / "from-script.sqlite"
    assert mod.main(["backup", str(src), str(dest)]) == 0
    assert mod.main(["check", str(dest)]) == 0
    drill_dest = tmp_path / "drill.sqlite"
    assert mod.main(["restore-drill", str(src), str(drill_dest)]) == 0
    assert drill_dest.is_file()


def test_script_check_missing_exits_2(tmp_path: Path) -> None:
    mod = _load_script()
    assert mod.main(["check", str(tmp_path / "nope.sqlite")]) == 2
