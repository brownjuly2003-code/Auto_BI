"""A failed cleanup after an output collision preserves both owners and both reasons."""

from pathlib import Path

import pytest

import auto_bi.investigation.application as application
from tests.test_investigation_cli import ARTIFACT_NAMES, PLAN_FIXTURE, RESULT_FIXTURE, invoke


@pytest.mark.parametrize("entry", ["application", "cli"])
def test_collision_and_cleanup_failure_preserve_output_and_report_both_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    output = tmp_path / "output"
    sentinel = b"owned by another writer\x00\xff"
    cleanup_error = PermissionError("cleanup refused")
    staged_payloads: dict[str, bytes] = {}
    cleanup_paths: list[Path] = []
    original_write = Path.write_bytes

    def write(path: Path, payload: bytes) -> int:
        assert path.parent.parent == tmp_path
        assert path.parent.name.startswith(".investigation-")
        staged_payloads[path.name] = payload
        written = original_write(path, payload)
        if path.name == "brief.md":
            output.mkdir()
            original_write(output / "sentinel", sentinel)
        return written

    def refuse_cleanup(path: Path) -> None:
        cleanup_paths.append(Path(path))
        raise cleanup_error

    def forbid_publish(source: Path, target: Path) -> None:
        pytest.fail("publication must not be attempted after the output collision")

    raised: application.OfflineInvestigationError | None = None
    with monkeypatch.context() as failure:
        failure.setattr(Path, "write_bytes", write)
        failure.setattr(application.shutil, "rmtree", refuse_cleanup)
        failure.setattr(application.os, "rename", forbid_publish)
        if entry == "application":
            with pytest.raises(application.OfflineInvestigationError) as caught:
                application.write_offline_investigation(PLAN_FIXTURE, [RESULT_FIXTURE], output)
            raised = caught.value
        else:
            assert invoke(output) == 2

    assert len(cleanup_paths) == 1
    staging = cleanup_paths[0]
    # Verify the owned location before removing it with the real cleanup function below.
    assert staging.resolve().parent == tmp_path.resolve()
    assert staging.name.startswith(".investigation-")
    try:
        reason = (
            f"output directory already exists: {output}; "
            f"cannot clean temporary output directory {staging}: cleanup refused"
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        if raised is not None:
            assert str(raised) == reason
            assert raised.__cause__ is cleanup_error
            assert captured.err == ""
        else:
            assert captured.err == f"error: {reason}\n"
        assert {path.name for path in output.iterdir()} == {"sentinel"}
        assert (output / "sentinel").read_bytes() == sentinel
        assert set(staged_payloads) == ARTIFACT_NAMES
        assert {path.name: path.read_bytes() for path in staging.iterdir()} == staged_payloads
        assert set(tmp_path.iterdir()) == {output, staging}
    finally:
        application.shutil.rmtree(staging)
    assert (output / "sentinel").read_bytes() == sentinel
