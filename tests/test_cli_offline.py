"""Offline CLI command tests: the eval-advisor and gaps paths that need no live DWH.

The advisor eval suite is deterministic (rule-pack findings, no LLM/stand) and the gaps
report runs offline with `run_query=None`, so both commands execute end-to-end here against
the committed demo model. This locks the two largest untested regions of cli.py (the eval
and gaps command bodies) without faking — real dispatch, real reports.

The build/chat/serve and golden-eval paths are intentionally not covered here: they require
the ClickHouse/Superset/DataLens stand or a live GraceKelly and are exercised on the Mac
stand (integration markers), not in the hermetic offline suite.
"""

from pathlib import Path

import pytest

from auto_bi import __version__
from auto_bi.cli import main

MODEL = "semantic/model.yaml"


# --- --version (D-1: `auto_bi --version`) ------------------------------------------


def test_version_flag_prints_version_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# --- eval --suite advisor (deterministic, offline) ---------------------------------


def test_eval_advisor_suite_passes_offline() -> None:
    # mirrors the CI step `auto_bi eval --suite advisor`: the demo model must not
    # regress any golden anti-pattern verdict (invariant 8, machinized).
    assert main(["eval", "--suite", "advisor", "--model-path", MODEL]) == 0


def test_eval_advisor_subset_via_cases() -> None:
    # the --cases filter narrows to a single advisor case id
    rc = main(
        ["eval", "--suite", "advisor", "--model-path", MODEL, "--cases", "ap1_no_filter_large_fact"]
    )
    assert rc == 0


def test_eval_missing_model_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    assert main(["eval", "--suite", "advisor", "--model-path", str(missing)]) == 2


# --- gaps --offline ---------------------------------------------------------------


def test_gaps_offline_to_stdout() -> None:
    assert main(["gaps", "--offline", "--model-path", MODEL]) == 0


def test_gaps_offline_to_file(tmp_path: Path) -> None:
    out = tmp_path / "sub" / "gaps.md"
    rc = main(["gaps", "--offline", "--model-path", MODEL, "--output", str(out)])
    assert rc == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8").strip()  # non-empty markdown


def test_gaps_missing_model_returns_2(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    assert main(["gaps", "--offline", "--model-path", str(missing)]) == 2
