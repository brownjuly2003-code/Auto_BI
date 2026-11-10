"""An unencodable output path is refused without leaving staged investigation files."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest

from auto_bi.investigation import application
from auto_bi.investigation.application import OfflineInvestigationError, write_offline_investigation
from tests.test_investigation_cli import (
    ARTIFACT_NAMES,
    PLAN_FIXTURE,
    RESULT_FIXTURE,
    invoke,
    skip_where_the_filesystem_refuses,
)


def _refuse_output(
    monkeypatch: pytest.MonkeyPatch, output: Path, phase: str, error: Exception
) -> list[Path]:
    staged: list[Path] = []
    if phase == "parent":
        original_mkdir = Path.mkdir

        def mkdir(self: Path, *args: Any, **kwargs: Any) -> None:
            if self == output.parent:
                raise error
            original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", mkdir)
    else:
        original_rename = application.os.rename

        def rename(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
            if Path(target) == output:
                stage = Path(source)
                assert {path.name for path in stage.iterdir()} == ARTIFACT_NAMES
                assert all(path.read_bytes() for path in stage.iterdir())
                staged.append(stage)
                raise error
            original_rename(source, target, *args, **kwargs)

        monkeypatch.setattr(application.os, "rename", rename)
    return staged


def _encoding_error() -> UnicodeEncodeError:
    return UnicodeEncodeError("utf-8", chr(0xD800), 0, 1, "private\nfilesystem detail")


@pytest.mark.parametrize("phase", ["parent", "publish"])
@pytest.mark.parametrize("encoding", ["ascii", "utf-8"])
@pytest.mark.parametrize("control", ["", "\n", "\r"], ids=["plain", "linefeed", "return"])
def test_output_encoding_refusal_exits_cleanly_and_removes_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    phase: str,
    encoding: str,
    control: str,
) -> None:
    refused = tmp_path / f"output-{control}{chr(0xD800)}"
    output = refused / "out" if phase == "parent" else refused
    staged = _refuse_output(monkeypatch, output, phase, _encoding_error())
    raw = io.BytesIO()
    with (
        io.TextIOWrapper(
            raw, encoding=encoding, errors="strict", newline="\n", write_through=True
        ) as stderr,
        monkeypatch.context() as streams,
    ):
        streams.setattr(sys, "stderr", stderr)
        assert invoke(output) == 2
        expected = f"error: cannot write output directory {str(output)!r}: UnicodeEncodeError\n"
        assert len(raw.getvalue().splitlines()) == 1
        assert raw.getvalue() == expected.encode(encoding, errors="backslashreplace")
    assert capsys.readouterr().out == ""
    assert len(staged) == (1 if phase == "publish" else 0)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("phase", ["parent", "publish"])
def test_output_encoding_refusal_preserves_cause_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    output = tmp_path / "parent" / "out"
    error = _encoding_error()
    with monkeypatch.context() as failure:
        staged = _refuse_output(failure, output, phase, error)
        with pytest.raises(OfflineInvestigationError) as raised:
            write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
    assert raised.value.__cause__ is error
    assert str(raised.value) == f"cannot write output directory {output}: UnicodeEncodeError"
    assert not output.exists()
    assert all(not path.exists() for path in staged)
    write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
    assert {path.name for path in output.iterdir()} == ARTIFACT_NAMES


def test_cleanup_failure_keeps_both_reasons_without_exposing_encoding_error_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "out"
    error = _encoding_error()
    cleanup_error = PermissionError("cleanup refused")

    def refuse_cleanup(path: Any, *args: Any, **kwargs: Any) -> None:
        raise cleanup_error

    with monkeypatch.context() as failure:
        staged = _refuse_output(failure, output, "publish", error)
        failure.setattr(application.shutil, "rmtree", refuse_cleanup)
        with pytest.raises(OfflineInvestigationError) as raised:
            write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
    assert len(staged) == 1
    assert raised.value.__cause__ is cleanup_error
    assert str(raised.value) == (
        f"cannot write output directory {output}: UnicodeEncodeError; "
        f"cannot clean temporary output directory {staged[0]}: cleanup refused"
    )
    assert not output.exists()
    assert {path.name for path in staged[0].iterdir()} == ARTIFACT_NAMES
    application.shutil.rmtree(staged[0])


def test_existing_output_is_preserved_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "user.txt"
    sentinel.write_bytes(b"user data")
    staged = _refuse_output(monkeypatch, output, "publish", _encoding_error())
    assert invoke(output) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: output directory already exists: {output}\n"
    assert staged == []
    assert list(tmp_path.iterdir()) == [output]
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"user data"


@pytest.mark.parametrize("name", ["результат-😀", f"result-{chr(0xDCFF)}"])
def test_encodable_output_paths_keep_artifact_bytes(tmp_path: Path, name: str) -> None:
    baseline = tmp_path / "baseline"
    output = tmp_path / name
    skip_where_the_filesystem_refuses(output)
    write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], baseline)
    write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == {
        path.name: path.read_bytes() for path in baseline.iterdir()
    }
    assert set(tmp_path.iterdir()) == {baseline, output}


@pytest.mark.parametrize(
    "error", [ValueError("unexpected"), UnicodeTranslateError("x", 0, 1, "unexpected")]
)
def test_unexpected_publication_errors_still_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    output = tmp_path / "out"
    with monkeypatch.context() as failure:
        staged = _refuse_output(failure, output, "publish", error)
        with pytest.raises(type(error)) as raised:
            write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
    assert raised.value is error
    assert not output.exists()
    for path in staged:
        application.shutil.rmtree(path)
