"""Help survives narrow output encodings; other parser messages keep their contract."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from typing import TextIO

import pytest

from auto_bi.cli import _HelpArgumentParser, main

ENCODINGS = ["ascii", "latin-1", "cp1252", "cp1251", "utf-8"]
CASES = [
    pytest.param(["--help"], 0, id="root-help"),
    pytest.param(["decision", "--help"], 0, id="decision-help"),
    pytest.param(["decision", "verify", "--help"], 0, id="verify-help"),
    pytest.param(["eval", "--help"], 0, id="v1-help"),
    pytest.param(["dbt-import", "--help"], 0, id="dbt-help"),
    pytest.param(["--version"], 0, id="version"),
    pytest.param(["unknown"], 2, id="root-choice"),
    pytest.param(["decision", "unknown"], 2, id="decision-choice"),
    pytest.param(
        ["decision", "validate", "missing.yaml", "--unknown"],
        2,
        id="unknown-option",
    ),
    pytest.param(["build"], 2, id="post-parse-error"),
]


def _invoke(monkeypatch: pytest.MonkeyPatch, argv: list[str], out: TextIO, err: TextIO) -> int:
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err)
        with pytest.raises(SystemExit) as ended:
            main(argv)
        assert sys.stdout is out
        assert sys.stderr is err
    assert isinstance(ended.value.code, int)
    return ended.value.code


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize(("argv", "code"), CASES)
def test_parser_preserves_text_destinations_and_exit_codes(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], code: int, encoding: str
) -> None:
    reference_out, reference_err = io.StringIO(), io.StringIO()
    assert _invoke(monkeypatch, argv, reference_out, reference_err) == code
    assert bool(reference_out.getvalue()) == (code == 0)
    assert bool(reference_err.getvalue()) == (code == 2)
    if argv == ["--help"]:
        assert "—" in reference_out.getvalue()
    out_bytes, err_bytes = io.BytesIO(), io.BytesIO()
    with (
        io.TextIOWrapper(out_bytes, encoding=encoding, errors="strict", newline="") as out,
        io.TextIOWrapper(err_bytes, encoding=encoding, errors="strict", newline="") as err,
    ):
        assert _invoke(monkeypatch, argv, out, err) == code
        out.flush()
        err.flush()
        assert out.errors == err.errors == "strict"
        assert out.encoding == err.encoding == encoding
        # Full byte comparison also catches duplicated usage/help and extra newlines.
        assert out_bytes.getvalue() == reference_out.getvalue().encode(encoding, "backslashreplace")
        assert err_bytes.getvalue() == reference_err.getvalue().encode(encoding, "backslashreplace")


@pytest.mark.parametrize("encoding", ["ascii", "latin-1"])
def test_help_in_a_real_process_exits_successfully(encoding: str) -> None:
    ended = subprocess.run(
        [sys.executable, "-m", "auto_bi.cli", "--help"],
        capture_output=True,
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING=encoding),
        check=False,
        timeout=30,
    )
    assert ended.returncode == 0, ended.stderr.decode("ascii", "backslashreplace")
    assert ended.stderr == b""
    assert b"usage: auto_bi" in ended.stdout
    assert b"\\u2014" in ended.stdout
    assert b"Traceback" not in ended.stdout


class _UnnamedASCII(io.StringIO):
    def write(self, text: str) -> int:
        text.encode("ascii")
        return super().write(text)


@pytest.mark.parametrize("encoding", ["ascii", "utf-8"])
def test_help_uses_the_explicit_stream(capsys: pytest.CaptureFixture[str], encoding: str) -> None:
    description = "Report — данные 😀"
    reference = io.StringIO()
    argparse.ArgumentParser(prog="auto_bi", description=description).print_help(file=reference)
    parser = _HelpArgumentParser(prog="auto_bi", description=description)
    payload = io.BytesIO()
    with io.TextIOWrapper(payload, encoding=encoding, errors="strict", newline="") as stream:
        parser.print_help(file=stream)
        stream.flush()
        assert payload.getvalue() == reference.getvalue().encode(encoding, "backslashreplace")
        assert stream.encoding == encoding
        assert stream.errors == "strict"
    assert capsys.readouterr() == ("", "")


def test_parser_can_use_a_stream_without_an_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _UnnamedASCII(), _UnnamedASCII()
    assert _invoke(monkeypatch, ["--help"], out, err) == 0
    assert "\\u2014" in out.getvalue()
    assert err.getvalue() == ""


@pytest.mark.parametrize("error_type", [ValueError, OSError, BrokenPipeError])
def test_unexpected_help_stream_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    class BrokenStream(io.StringIO):
        def write(self, text: str) -> int:
            raise error_type("closed output")

    monkeypatch.setattr(sys, "stdout", BrokenStream())
    with pytest.raises(error_type, match="closed output"):
        main(["--help"])
