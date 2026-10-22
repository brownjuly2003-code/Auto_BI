"""A refused input filename is a usage error before an investigation writes anything."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from auto_bi.investigation.application import OfflineInvestigationError, write_offline_investigation
from tests.test_investigation_cli import PLAN_FIXTURE, RESULT_FIXTURE, invoke


def _refuse_read(monkeypatch: pytest.MonkeyPatch, path: Path, error: Exception) -> list[Path]:
    reads: list[Path] = []
    original = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        reads.append(self)
        if self == path:
            raise error
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    return reads


@pytest.mark.parametrize("which", ["plan", "result"])
@pytest.mark.parametrize("encoding", ["ascii", "utf-8"])
def test_unencodable_input_path_exits_before_output_and_later_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    which: str,
    encoding: str,
) -> None:
    refused = tmp_path / f"input-{chr(0xD800)}.yaml"
    error = UnicodeEncodeError("utf-8", chr(0xD800), 0, 1, "private\nfilesystem detail")
    reads = _refuse_read(monkeypatch, refused, error)
    plan = refused if which == "plan" else PLAN_FIXTURE
    results = (RESULT_FIXTURE, refused, tmp_path / "must-not-read.yaml")
    output = tmp_path / "missing-parent" / "out"
    raw = io.BytesIO()
    with (
        io.TextIOWrapper(
            raw, encoding=encoding, errors="strict", newline="\n", write_through=True
        ) as stderr,
        monkeypatch.context() as streams,
    ):
        streams.setattr(sys, "stderr", stderr)

        assert invoke(output, plan, *results) == 2

        expected = f"error: cannot read {refused}: UnicodeEncodeError\n"
        assert raw.getvalue() == expected.encode(encoding, errors="backslashreplace")
    assert capsys.readouterr().out == ""
    assert reads == ([refused] if which == "plan" else [PLAN_FIXTURE, RESULT_FIXTURE, refused])
    assert not output.parent.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("which", ["plan", "result"])
def test_unencodable_input_path_preserves_the_read_failure_as_its_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    refused = tmp_path / "input.yaml"
    error = UnicodeEncodeError("utf-8", chr(0xD800), 0, 1, "filename refused")
    _refuse_read(monkeypatch, refused, error)
    plan = refused if which == "plan" else PLAN_FIXTURE
    result = refused if which == "result" else RESULT_FIXTURE

    with pytest.raises(OfflineInvestigationError) as raised:
        write_offline_investigation(plan, [result], tmp_path / "out")

    assert str(raised.value) == f"cannot read {refused}: UnicodeEncodeError"
    assert raised.value.__cause__ is error
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("which", ["plan", "result"])
@pytest.mark.parametrize(
    "error", [RuntimeError("loader defect"), UnicodeTranslateError("x", 0, 1, "unexpected")]
)
def test_other_input_failures_still_propagate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str, error: Exception
) -> None:
    refused = tmp_path / "input.yaml"
    _refuse_read(monkeypatch, refused, error)
    plan = refused if which == "plan" else PLAN_FIXTURE
    result = refused if which == "result" else RESULT_FIXTURE

    with pytest.raises(type(error)) as raised:
        invoke(tmp_path / "out", plan, result)

    assert raised.value is error
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("suffix", ["😀", chr(0xDCFF)], ids=["unicode", "surrogateescape"])
def test_readable_unicode_input_names_keep_the_artifact_bytes(tmp_path: Path, suffix: str) -> None:
    plan = tmp_path / f"план-{suffix}.yaml"
    result = tmp_path / f"результат-{suffix}.yaml"
    plan.write_bytes(PLAN_FIXTURE.read_bytes())
    result.write_bytes(RESULT_FIXTURE.read_bytes())
    baseline = tmp_path / "baseline"
    named = tmp_path / "named"

    assert invoke(baseline) == 0
    assert invoke(named, plan, result) == 0

    assert {path.name: path.read_bytes() for path in named.iterdir()} == {
        path.name: path.read_bytes() for path in baseline.iterdir()
    }
