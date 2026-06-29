"""Smoke test for the golden-path demo (scripts/demo_golden_path.py).

The demo is a portfolio artifact a reviewer runs from a clean clone, so CI keeps it
green: it exercises the real offline pipeline (autospec -> normalize -> validate ->
SQL_GEN -> advisor), and this guards its own glue against API drift. Run as a subprocess
because scripts/ is not an importable package.
"""

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "demo_golden_path.py"


def _coverage_free_env() -> dict[str, str]:
    """The parent env minus the variables that make `coverage` auto-start in a child process.

    The demo runs as a subprocess and is a glue smoke test, NOT a coverage source (the pipeline
    it exercises is already measured by in-process tests). When pytest-cov propagates these to a
    child, a `--cov` run also instruments this subprocess, and on Windows's pure-Python tracer
    that ran it ~28x slower (≈410s vs ≈14s) and blew the timeout. Stripping them keeps the smoke
    fast under ANY pytest-cov version — the isolation no longer relies on the runner happening
    not to forward them.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if k != "COVERAGE_PROCESS_START" and not k.startswith("COV_CORE_")
    }


def test_demo_runs_and_shows_the_pipeline() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=_coverage_free_env(),
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # the four acts each leave a recognizable marker
    assert "Semantic model" in out
    assert "validate_spec: 0 errors" in out  # auto-overview compiles to a valid spec
    assert "LEFT JOIN" in out  # compiled SQL shows the id->name label join
    assert "dm_change_request" in out  # the advisor's headline differentiator verdict
