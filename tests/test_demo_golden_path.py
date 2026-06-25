"""Smoke test for the golden-path demo (scripts/demo_golden_path.py).

The demo is a portfolio artifact a reviewer runs from a clean clone, so CI keeps it
green: it exercises the real offline pipeline (autospec -> normalize -> validate ->
SQL_GEN -> advisor), and this guards its own glue against API drift. Run as a subprocess
because scripts/ is not an importable package.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "demo_golden_path.py"


def test_demo_runs_and_shows_the_pipeline() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    # the four acts each leave a recognizable marker
    assert "Semantic model" in out
    assert "validate_spec: 0 errors" in out  # auto-overview compiles to a valid spec
    assert "LEFT JOIN" in out  # compiled SQL shows the id->name label join
    assert "dm_change_request" in out  # the advisor's headline differentiator verdict
