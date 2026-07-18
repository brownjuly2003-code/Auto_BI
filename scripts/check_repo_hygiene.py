#!/usr/bin/env python
"""Repo hygiene gate — keep internal working notes out of the public tree.

The working tree deliberately carries internal-only files next to the public code:
session handoffs and ops scripts (root ``_*``: ``_NEXT_SESSION.md``, ``_demo_*``,
``_live_*`` verify scripts, ``_tools/``), root ``audit_*.md`` / ``plan_*.md``
remediation docs, presentation drafts and research scratches. All of them are
ignored via ``.gitignore``, but ``git add -f`` or a future ``.gitignore`` edit could
silently re-introduce them — and anything tracked ends up in the public repo, the
sdist and the HF Space snapshot. This gate fails CI the moment any such path shows
up in ``git ls-files``, so the cleanup can't rot.

Layer 1 is ``.gitignore`` (keeps files out of the index), layer 2 is this gate
(fails CI if one gets in anyway); the publish filter (tracked-files-only snapshot
in ``deploy/hf-demo/publish_space.py``) is the third, host-side layer.

Usage::

    python scripts/check_repo_hygiene.py             # scan tracked files, exit 1 on violations
    python scripts/check_repo_hygiene.py --self-test # prove the detector works
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Root-level exact names that are internal-only (infra details / presentation drafts).
_ROOT_NAMES = {
    "presentation.html",
    "dashboard_design_research.md",
    "p8-hf-demo.md",
    "start-bi-tunnel.cmd",
}

# Root-level internal scratch-tracker prefixes; every entry is (prefix, must_be_md).
_ROOT_PREFIXES = (
    ("audit_", True),
    ("plan_", True),  # plan.md (public roadmap) does NOT match — no underscore
    ("research", True),
    ("new_plen_", True),
    ("nl_sql_", True),
    ("s01-", True),
    ("x4-", True),
)


def classify(path: str) -> str | None:
    """Return a human-readable reason if *path* is forbidden, else ``None``.

    ``path`` is a forward-slash repo-relative path exactly as ``git ls-files``
    emits it. Only ROOT-level files are judged by name: nested ``docs/plans/**``
    stays public and package modules like ``auto_bi/llm/_structured.py`` are fine.
    """
    name = path.rsplit("/", 1)[-1]
    if name.endswith((".sqlite", ".sqlite3", ".db")):
        return "local database file"
    # A WAL/SHM/journal sidecar holds rows not yet checkpointed into the main
    # file, so committing one leaks working data just as the .sqlite would.
    if name.endswith(("-wal", "-shm", "-journal")):
        return "SQLite WAL/SHM/journal sidecar (holds uncheckpointed working data)"
    if path.split("/", 1)[0].startswith("_"):
        return "internal root-level scratch/handoff/ops file (leading underscore)"
    if "/" in path:
        return None
    if path in _ROOT_NAMES:
        return "internal root-level note (explicit denylist)"
    for prefix, must_be_md in _ROOT_PREFIXES:
        if path.startswith(prefix) and (not must_be_md or path.endswith(".md")):
            return f"internal root-level scratch tracker ({prefix}*)"
    return None


def find_violations(paths: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in paths:
        reason = classify(path)
        if reason is not None:
            out.append((path, reason))
    return out


def tracked_files() -> list[str]:
    # core.quotepath=false → non-ASCII paths (e.g. cyrillic doc names) come
    # through as raw UTF-8 instead of octal-escaped, quoted strings.
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line]


def self_test() -> int:
    """Prove the detector flags forbidden paths and leaves legitimate ones alone."""
    must_flag = [
        "_NEXT_SESSION.md",
        "_demo_keepalive.py",
        "_tools/backup.cmd",
        "_live_prune_verify.py",
        "audit_fable_18_07_26.md",
        "plan_fable_18_07_26.md",
        "plan_for_pres.md",
        "presentation.html",
        "research.md",
        "new_plen_05_07_26.md",
        "nl_sql_decision_03_07_26.md",
        "s01-text-first-core.md",
        "x4-session-resume.md",
        "p8-hf-demo.md",
        "dashboard_design_research.md",
        "start-bi-tunnel.cmd",
        "store/auto_bi.sqlite",
        "store/auto_bi.sqlite-wal",
        "tmp/scratch.db",
    ]
    must_pass = [
        "README.md",
        "plan.md",  # the public roadmap: no underscore, not plan_*
        "CHANGELOG.md",
        "docs/plans/2026-07-18-live-cleanup-wiring.md",  # nested plans are public
        "docs/audit_notes.md",  # nested audit_* is allowed (only root is internal)
        "auto_bi/__init__.py",  # leading underscore only forbidden at ROOT
        "auto_bi/llm/_structured.py",
        "scripts/check_repo_hygiene.py",
        "docs/journal.md",  # ends in 'journal' but not '-journal': a doc, not a sidecar
        "research/notes.txt",  # nested dir, not a root research*.md
    ]
    failures: list[str] = []
    for path in must_flag:
        if classify(path) is None:
            failures.append(f"FAILED to flag forbidden path: {path!r}")
    for path in must_pass:
        reason = classify(path)
        if reason is not None:
            failures.append(f"FALSE POSITIVE on legitimate path: {path!r} -> {reason}")
    if failures:
        print("[repo-hygiene] self-test FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(f"[repo-hygiene] self-test passed ({len(must_flag)} flagged, {len(must_pass)} clean).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Verify the detector flags forbidden paths and passes clean ones, then exit.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    paths = tracked_files()
    violations = find_violations(paths)
    if violations:
        print(
            f"[repo-hygiene] FAILED: {len(violations)} internal-only file(s) tracked in the repo:",
            file=sys.stderr,
        )
        for path, reason in violations:
            print(f"  {path}  ({reason})", file=sys.stderr)
        print(
            "\nInternal notes never ship in the public tree. Remove with "
            "`git rm --cached <path>` and confirm `.gitignore` still covers them.",
            file=sys.stderr,
        )
        return 1

    print(f"[repo-hygiene] OK: {len(paths)} tracked file(s), no internal-note leaks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
