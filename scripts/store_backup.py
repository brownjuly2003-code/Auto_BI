#!/usr/bin/env python
"""SQLite store backup / integrity / restore-drill (plan_sol step 12).

Operators: prefer this over ``cp`` of a live DB file (torn read risk).
Uses the same online backup path as ``Store.backup_to`` (stdlib Connection.backup).

Usage::

    uv run python scripts/store_backup.py backup data/auto_bi.sqlite /backup/auto_bi.sqlite
    uv run python scripts/store_backup.py check /backup/auto_bi.sqlite
    uv run python scripts/store_backup.py restore-drill \\
        data/auto_bi.sqlite /tmp/auto_bi-drill.sqlite
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# scripts/ is not a package — import installed auto_bi (uv run).
from auto_bi.store import Store


def cmd_backup(src: Path, dest: Path) -> int:
    if not src.is_file():
        print(f"source missing: {src}", file=sys.stderr)
        return 2
    store = Store(src)
    try:
        if not store.is_integrity_ok():
            print(f"source integrity failed: {store.integrity_check()}", file=sys.stderr)
            return 1
        out = store.backup_to(dest)
    finally:
        store.close()
    # Verify destination independently
    check = Store(out)
    try:
        ok = check.is_integrity_ok()
        detail = check.integrity_check()
    finally:
        check.close()
    if not ok:
        print(f"backup written but integrity failed: {detail}", file=sys.stderr)
        return 1
    print(f"ok: backed up {src} -> {out} (integrity={detail})")
    return 0


def cmd_check(path: Path) -> int:
    if not path.is_file():
        print(f"missing: {path}", file=sys.stderr)
        return 2
    store = Store(path)
    try:
        detail = store.integrity_check()
        ok = detail == ["ok"]
    finally:
        store.close()
    if not ok:
        print(f"FAIL integrity: {detail}", file=sys.stderr)
        return 1
    print(f"ok: {path} integrity={detail}")
    return 0


def cmd_restore_drill(src: Path, dest: Path | None) -> int:
    """Backup src → dest (or temp), open dest, integrity + application smoke."""
    if not src.is_file():
        print(f"source missing: {src}", file=sys.stderr)
        return 2
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    if dest is None:
        tmp_dir = tempfile.TemporaryDirectory(prefix="auto_bi_restore_")
        dest = Path(tmp_dir.name) / "restored.sqlite"
    try:
        src_store = Store(src)
        try:
            # Seed a marker session if the DB is empty so smoke has something to read
            # (real ops DBs already have sessions — empty still must restore cleanly).
            sessions_before = src_store._rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
            marker_id: str | None = None
            if sessions_before == 0:
                marker_id = src_store.create_session("restore-drill-marker")
            src_store.backup_to(dest)
        finally:
            src_store.close()

        restored = Store(dest)
        try:
            if not restored.is_integrity_ok():
                print(
                    f"FAIL restore integrity: {restored.integrity_check()}",
                    file=sys.stderr,
                )
                return 1
            n = restored._rows("SELECT COUNT(*) AS n FROM sessions")[0]["n"]
            if n < 1:
                print("FAIL restore smoke: no sessions in backup", file=sys.stderr)
                return 1
            # Application smoke: write + read on the restored file
            sid = restored.create_session("restore-drill-post-open")
            restored.add_message(sid, "user", "smoke")
            msgs = restored.messages(sid)
            if not msgs or msgs[0]["content"] != "smoke":
                print("FAIL restore smoke: message roundtrip", file=sys.stderr)
                return 1
            if marker_id is not None:
                # original marker should still be present
                rows = restored._rows("SELECT id FROM sessions WHERE id = ?", marker_id)
                if not rows:
                    print("FAIL restore smoke: marker session missing", file=sys.stderr)
                    return 1
        finally:
            restored.close()
        print(f"ok: restore-drill {src} -> {dest} (sessions>={n}, integrity ok)")
        return 0
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", help="Online backup src -> dest + integrity check")
    b.add_argument("src", type=Path)
    b.add_argument("dest", type=Path)

    c = sub.add_parser("check", help="PRAGMA integrity_check on a store file")
    c.add_argument("path", type=Path)

    r = sub.add_parser(
        "restore-drill",
        help="Backup then open dest and run integrity + session smoke",
    )
    r.add_argument("src", type=Path)
    r.add_argument(
        "dest",
        type=Path,
        nargs="?",
        default=None,
        help="optional dest path (temp file if omitted)",
    )

    args = p.parse_args(argv)
    if args.cmd == "backup":
        return cmd_backup(args.src, args.dest)
    if args.cmd == "check":
        return cmd_check(args.path)
    if args.cmd == "restore-drill":
        return cmd_restore_drill(args.src, args.dest)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
