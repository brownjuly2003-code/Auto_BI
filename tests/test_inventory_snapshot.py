"""E3: repository mapping, transactional refresh and the offline PR input path."""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from auto_bi.adapters.superset.client import SupersetClient
from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.use_cases import InventoryRequest, VerifyError, inventory
from auto_bi.inventory import superset as reader
from tests.test_superset_inventory import PILOT, Recorded, _answers, _semantic

REF = "dashboard://sales/weekly@1"


def test_cli_passes_map_and_replace_to_the_use_case(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[InventoryRequest] = []

    def capture(request: InventoryRequest) -> use_cases.InventoryReport:
        requests.append(request)
        return use_cases.InventoryReport()

    monkeypatch.setattr(use_cases, "inventory", capture)
    assert (
        main(
            [
                "decision",
                "inventory",
                "semantic",
                "--map",
                "map.json",
                "--replace",
                "--database",
                "2",
                "--output",
                "out",
            ]
        )
        == 0
    )
    assert len(requests) == 1
    assert requests[0].mapping == "map.json"
    assert requests[0].replace is True
    assert requests[0].database == 2


def test_documented_inventory_commands_reach_the_use_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path(__file__).resolve().parents[1] / "docs/specs/superset-inventory.md"
    usage = path.read_text(encoding="utf-8").split("## Usage\n", 1)[1].replace("\\\n", " ")
    requests: list[InventoryRequest] = []

    def capture(request: InventoryRequest) -> use_cases.InventoryReport:
        requests.append(request)
        return use_cases.InventoryReport()

    monkeypatch.setattr(use_cases, "inventory", capture)
    for line in usage.splitlines():
        if line.startswith("AUTO_BI_NO_ENV_FILE=1 uv run"):
            args = shlex.split(line)
            assert args[:5] == ["AUTO_BI_NO_ENV_FILE=1", "uv", "run", "--no-env-file", "auto_bi"]
            assert main(args[5:]) == 0
    assert len(requests) == 2
    assert [r.replace for r in requests] == [False, True]
    assert all(r.mapping == "superset-map.json" and r.database == 2 for r in requests)


@pytest.mark.parametrize(
    "payload",
    [
        '{"dashboards": [], "extra": 1}',
        '{"dashboards": [{"ref": "dashboard://a@1", "superset": "12"}], "dashboards": []}',
        '{"dashboards": [], "dashboards": [{"ref": "dashboard://a@1", "superset": "12"}]}',
        '{"dashboards": [{"ref": "dashboard://a@1", "superset": "1", "superset": "12"}]}',
        '{"dashboards": [{"ref": "dashboard://a@1", "superset": 12}]}',
        '{"dashboards": [{"ref": "dashboard://a@1", "superset": "12", "x": 1}]}',
        '{"dashboards": []}',
        "[]",
        "{",
        json.dumps({"dashboards": [{"ref": REF, "superset": "\udcff"}]}),
        json.dumps({"dashboards": [{"ref": REF, "superset": " "}]}),
        json.dumps({"dashboards": [{"ref": "dashboard://missing_version", "superset": "12"}]}),
        json.dumps({"dashboards": [{"ref": "metric://a@1", "superset": "12"}]}),
        json.dumps({"dashboards": [{"ref": REF, "superset": "12"}] * 2}),
    ],
)
def test_invalid_mapping_without_filesystem(
    monkeypatch: pytest.MonkeyPatch,
    payload: str,
) -> None:
    def read(self: Path, **kwargs: Any) -> str:
        assert self == Path("map.json") and kwargs == {"encoding": "utf-8"}
        return payload

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid map reached output or source")

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(use_cases.filesystem, "exists", forbidden)
    request = InventoryRequest("semantic", [], "out", forbidden, mapping="map.json")
    with pytest.raises(VerifyError):
        inventory(request)


def test_mapping_decoding_failure_without_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    def read(self: Path, **kwargs: Any) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(Path, "read_text", read)
    request = InventoryRequest("semantic", [], "out", Recorded, mapping="map.json")
    with pytest.raises(VerifyError, match="expected UTF-8 JSON"):
        inventory(request)


def test_reader_report_version_is_positive_and_d6_defaults_to_one() -> None:
    assert use_cases.InventoryReport.model_validate_json('{"dashboards": []}').reader_version == 1
    assert use_cases.InventoryReport(reader_version=999).reader_version == 999
    for bad in (0, -1, True, "1"):
        with pytest.raises(ValueError):
            use_cases.InventoryReport.model_validate({"reader_version": bad})


def test_windows_reparse_entry_is_refused_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(use_cases, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(
            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            st_mode=stat.S_IFDIR,
        ),
    )
    assert use_cases._inventory_entry(Path("junction"), directory=True) is False


def test_recovery_os_refusal_is_a_safe_exit_two_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(self: Path, **kwargs: Any) -> None:
        if self.name.startswith(".out."):
            raise KeyboardInterrupt

    def denied(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(Path, "mkdir", interrupt)
    monkeypatch.setattr(use_cases.filesystem, "exists", lambda path: True)
    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(VerifyError, match=r"cannot restore inventory .*recovery paths:"):
        use_cases._write_inventory(Path("out"), use_cases.InventoryReport(), {})


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _map(root: Path, pairs: list[tuple[str, str]]) -> Path:
    path = root / "map.json"
    payload = {"dashboards": [{"ref": r, "superset": i} for r, i in pairs]}
    path.write_bytes(json.dumps(payload).encode())
    return path


@pytest.fixture
def snapshot(tmp_path: Path) -> InventoryRequest:
    request = InventoryRequest(
        semantic=_semantic(tmp_path),
        dashboards=[(REF, "12")],
        output=tmp_path / "out",
        source=Recorded,
    )
    inventory(request)
    return request


@pytest.mark.parametrize(
    "payload",
    [
        b'{"dashboards": [], "extra": 1}',
        b'{"dashboards": [{"ref": "dashboard://a@1", "superset": "12", "extra": 1}]}',
        b'{"dashboards": [{"ref": "dashboard://a@1", "superset": 12}]}',
        b'{"dashboards": [{"ref": "dashboard://a@1"}]}',
        b'{"dashboards": [], "dashboards": [{"ref": "dashboard://a@1", "superset": "12"}]}',
        b'{"dashboards": [{"ref": "dashboard://a@1", "superset": "1", "superset": "12"}]}',
        b'{"dashboards": []}',
        b"[]",
        b"{",
        b"\xff",
    ],
)
def test_map_refusal_precedes_source_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    payload: bytes,
) -> None:
    path = tmp_path / "map.json"
    path.write_bytes(payload)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid map touched output or source")

    monkeypatch.setattr(use_cases.filesystem, "exists", forbidden)
    monkeypatch.setattr(SupersetClient, "__init__", forbidden)
    code = main(
        [
            "decision",
            "inventory",
            str(tmp_path),
            "--map",
            str(path),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2
    assert capsys.readouterr().err.startswith("error:")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["map.json"]


@pytest.mark.parametrize(
    "pairs",
    [
        [("weekly", "12")],
        [("metric://sales/revenue@1", "12")],
        [("dashboard://a", "12")],
        [("dashboard://../a@1", "12")],
        [(REF, " ")],
        [(REF, "\udcff")],
        [(REF, "12"), (REF, "11")],
        [("dashboard://a@" + "9" * 5000, "12")],  # past int()'s digit limit: still exit 2
    ],
)
def test_map_and_flags_share_the_same_pair_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pairs: list[tuple[str, str]],
) -> None:
    path = _map(tmp_path, pairs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("invalid pair touched output or source")

    monkeypatch.setattr(use_cases.filesystem, "exists", forbidden)
    monkeypatch.setattr(SupersetClient, "__init__", forbidden)
    base = ["decision", "inventory", str(tmp_path), "--output", str(tmp_path / "out")]
    assert main([*base, "--map", str(path)]) == 2
    map_error = capsys.readouterr().err
    flags = [arg for ref, ident in pairs for arg in ("--dashboard", f"{ref}={ident}")]
    assert main([*base, *flags]) == 2
    assert capsys.readouterr().err == map_error


def test_map_cannot_mix_with_dashboard(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "decision",
                "inventory",
                str(tmp_path),
                "--map",
                "missing.json",
                "--dashboard",
                f"{REF}=12",
                "--output",
                str(tmp_path / "out"),
            ]
        )
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("database,code", [(2, 0), (1, 3)])
def test_map_accepts_database_and_writes_reader_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    database: int,
    code: int,
) -> None:
    monkeypatch.setattr(reader, "RestSupersetSource", lambda client: Recorded())
    path = _map(tmp_path, [(REF, "d6-live")])
    out = tmp_path / "out"
    assert (
        main(
            [
                "decision",
                "inventory",
                str(_semantic(tmp_path)),
                "--map",
                str(path),
                "--database",
                str(database),
                "--output",
                str(out),
            ]
        )
        == code
    )
    report = json.loads((out / "inventory.json").read_bytes())
    assert report["reader_version"] == 3
    assert report["superset_version"] == "4.1.2"
    assert (out / "sales/weekly@1.yaml").exists() == (code == 0)


@pytest.mark.parametrize("old_version", [None, 1, 2])
@pytest.mark.parametrize("ident", ["12", "11"])
def test_replace_whole_snapshot_including_d6(
    snapshot: InventoryRequest,
    tmp_path: Path,
    old_version: int | None,
    ident: str,
) -> None:
    out = Path(snapshot.output)
    path = out / "inventory.json"
    report = json.loads(path.read_bytes())
    if old_version is None:
        report.pop("reader_version", None)
    else:
        report["reader_version"] = old_version
    path.write_bytes(json.dumps(report).encode())
    report = inventory(replace(snapshot, dashboards=[(REF, ident)], replace=True))
    assert bool(report.refused) == (ident == "11")
    expected = {"inventory.json", "sales/weekly@1.yaml"} if ident == "12" else {"inventory.json"}
    assert set(_files(out)) == expected
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "semantic"]
    assert json.loads((out / "inventory.json").read_bytes())["reader_version"] == 3


@pytest.mark.parametrize(
    "entry", ["alien.txt", "empty", "sales/extra", "sales/weekly@1.yaml/child"]
)
def test_replace_refuses_first_alien_entry_before_source(
    snapshot: InventoryRequest,
    tmp_path: Path,
    entry: str,
) -> None:
    out = Path(snapshot.output)
    target = out / entry
    if entry.endswith("/child"):
        target.parent.unlink()
        target.parent.mkdir()
    if entry == "empty":
        target.mkdir()
    else:
        target.write_bytes(b"keep me")
    before = _files(out)

    def forbidden() -> Recorded:
        pytest.fail("unowned output reached Superset")

    with pytest.raises(VerifyError) as error:
        inventory(replace(snapshot, source=forbidden, replace=True))
    assert repr(entry.removesuffix("/child")) in str(error.value)
    assert _files(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "semantic"]


@pytest.mark.parametrize(
    "stage", ["mkdir", "write", "report", "old_rename", "new_rename", "cleanup"]
)
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
@pytest.mark.parametrize("after", [False, True])
def test_replace_rolls_back_every_mutating_step(
    snapshot: InventoryRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    error_type: type[BaseException],
    after: bool,
) -> None:
    out = Path(snapshot.output)
    before = _files(out)
    mkdir, write_bytes, rename, rmtree = Path.mkdir, Path.write_bytes, Path.rename, shutil.rmtree
    fired = False

    def inject(matches: bool, operation: Callable[[], Any]) -> Any:
        nonlocal fired
        if not matches or fired:
            return operation()
        fired = True
        if after:
            operation()
        raise error_type("injected")

    def write(self: Path, data: bytes) -> int:
        if not fired:
            assert _files(out) == before  # catches delete-old-then-write
        matches = stage == ("report" if self.name == "inventory.json" else "write")
        return inject(matches, lambda: write_bytes(self, data))

    def make(self: Path, *args: Any, **kwargs: Any) -> None:
        matches = stage == "mkdir" and self.name.startswith(".out.")
        inject(matches, lambda: mkdir(self, *args, **kwargs))

    def move(self: Path, target: Path) -> Path:
        assert self.parent == target.parent == out.parent
        matches = stage == ("old_rename" if self == out else "new_rename")
        return inject(matches, lambda: rename(self, target))

    def remove(path: Path, *args: Any, **kwargs: Any) -> None:
        inject(stage == "cleanup", lambda: rmtree(path, *args, **kwargs))

    monkeypatch.setattr(Path, "write_bytes", write)
    monkeypatch.setattr(Path, "mkdir", make)
    monkeypatch.setattr(Path, "rename", move)
    monkeypatch.setattr(shutil, "rmtree", remove)
    with pytest.raises(VerifyError if error_type is OSError else KeyboardInterrupt):
        inventory(replace(snapshot, dashboards=[("dashboard://new@2", "12")], replace=True))
    assert fired
    assert _files(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "semantic"]


def test_replace_dot_uses_sibling_staging(
    snapshot: InventoryRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = Path(snapshot.output)
    monkeypatch.chdir(out)
    seen: list[Path] = []

    def write(output: Path, *args: Any) -> None:
        seen.append(output)

    monkeypatch.setattr(use_cases, "_write_inventory", write)
    inventory(replace(snapshot, output=".", replace=True))
    assert seen == [out]  # Windows can refuse moving a directory used as the process cwd.
    assert sorted(p.name for p in out.parent.iterdir()) == ["out", "semantic"]


@pytest.mark.parametrize("backup", [False, True])
def test_existing_staging_or_backup_is_never_removed(
    snapshot: InventoryRequest,
    monkeypatch: pytest.MonkeyPatch,
    backup: bool,
) -> None:
    out = Path(snapshot.output)
    before = _files(out)
    monkeypatch.setattr(use_cases.secrets, "token_hex", lambda n: "c" * 16)
    name = ".out." + "c" * 16 + (".previous" if backup else "")
    occupied = out.parent / name
    occupied.mkdir()
    (occupied / "foreign").write_bytes(b"keep")
    with pytest.raises(VerifyError):
        inventory(replace(snapshot, replace=True))
    assert (occupied / "foreign").read_bytes() == b"keep"
    assert _files(out) == before
    assert sorted(p.name for p in out.parent.iterdir()) == sorted([name, "out", "semantic"])


def test_snapshot_changed_during_read_is_preserved(snapshot: InventoryRequest) -> None:
    out = Path(snapshot.output)

    def source() -> Recorded:
        (out / "foreign").write_bytes(b"concurrent edit")
        return Recorded()

    with pytest.raises(VerifyError, match="unexpected entry 'foreign'"):
        inventory(replace(snapshot, source=source, replace=True))
    assert (out / "foreign").read_bytes() == b"concurrent edit"
    assert sorted(p.name for p in out.parent.iterdir()) == ["out", "semantic"]


@pytest.mark.parametrize(
    "change",
    [
        "bad_json",
        "extra_key",
        "bad_written",
        "missing_spec",
        "missing_report",
        "no_report_keys",
        "duplicate_key",
    ],
)
def test_replace_requires_its_report_and_all_written_files(
    snapshot: InventoryRequest,
    change: str,
) -> None:
    out = Path(snapshot.output)
    path = out / "inventory.json"
    report = json.loads(path.read_bytes())
    if change == "bad_json":
        path.write_bytes(b"\xff")
    elif change == "extra_key":
        report["alien"] = True
        path.write_bytes(json.dumps(report).encode())
    elif change == "bad_written":
        report["dashboards"][0]["written"] = "../outside.yaml"
        path.write_bytes(json.dumps(report).encode())
    elif change == "missing_spec":
        (out / "sales/weekly@1.yaml").unlink()
    elif change == "missing_report":
        path.unlink()
    elif change == "no_report_keys":
        path.write_bytes(b"{}")
    else:
        path.write_bytes(path.read_bytes().replace(b"{", b'{"dashboards": [],', 1))
    before = _files(out)
    with pytest.raises(VerifyError) as error:
        inventory(replace(snapshot, replace=True))
    assert _files(out) == before
    if change == "missing_report":
        # A directory the inventory did not write is refused as such, not as a failed write.
        assert str(error.value) == (
            f"cannot replace inventory {out}: missing entry 'inventory.json'; "
            "--replace takes a directory an inventory wrote"
        )


@pytest.mark.parametrize("entry", ["inventory.json", "sales", "sales/weekly@1.yaml", "alien"])
def test_replace_refuses_links_without_following_them(
    snapshot: InventoryRequest,
    tmp_path: Path,
    entry: str,
) -> None:
    out = Path(snapshot.output)
    path = out / entry
    directory = entry == "sales"
    outside = tmp_path / "outside"
    if directory or path.exists():
        path.rename(outside)
    else:
        outside.write_bytes(b"keep")
    try:
        path.symlink_to(outside, target_is_directory=directory)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    before = _files(outside) if directory else outside.read_bytes()
    with pytest.raises(VerifyError, match="unexpected entry") as error:
        inventory(replace(snapshot, replace=True))
    assert repr(entry) in str(error.value)
    assert (_files(outside) if directory else outside.read_bytes()) == before
    assert path.is_symlink()


def test_replace_refuses_hard_link(snapshot: InventoryRequest, tmp_path: Path) -> None:
    path = Path(snapshot.output) / "sales/weekly@1.yaml"
    (tmp_path / "outside").hardlink_to(path)
    before = _files(Path(snapshot.output))
    with pytest.raises(VerifyError, match=r"unexpected entry 'sales/weekly@1\.yaml'"):
        inventory(replace(snapshot, replace=True))
    assert _files(Path(snapshot.output)) == before


@pytest.mark.parametrize("failure", ["model", "source", "answer", "unicode", "missing"])
def test_replace_exit_two_preserves_bytes_and_leaves_no_temporary_directory(
    snapshot: InventoryRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    out = Path(snapshot.output)
    before = _files(out)

    class Source(Recorded):
        def dashboard(self, ident: str) -> Any:
            if failure == "source":
                raise reader.SupersetSourceError("GET /api/v1/dashboard/12 -> HTTP 500")
            if failure == "answer":
                raise reader.SupersetAnswerError("no object")
            answer = dict(super().dashboard(ident))
            if failure == "unicode":
                answer["dashboard_title"] = "\ud800"
            return answer

    monkeypatch.setattr(reader, "RestSupersetSource", lambda client: Source())
    argv = [
        "decision",
        "inventory",
        str(tmp_path if failure == "model" else snapshot.semantic),
        "--dashboard",
        f"{REF}=12",
        "--output",
        str(tmp_path / "missing" if failure == "missing" else out),
        "--replace",
    ]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    if failure == "missing":
        assert err == (
            f"error: cannot replace inventory {tmp_path / 'missing'}: it does not exist; "
            "--replace takes a directory an inventory wrote" + chr(10)
        )
    assert _files(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out", "semantic"]


def test_partial_backup_cleanup_is_repaired(
    snapshot: InventoryRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = Path(snapshot.output)
    before = _files(out)
    rmtree = shutil.rmtree

    def partial(path: Path, *args: Any, **kwargs: Any) -> None:
        if str(path).endswith(".previous"):
            (path / "sales/weekly@1.yaml").unlink()
            (path / "sales").rmdir()
            raise OSError("partial cleanup")
        rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", partial)
    with pytest.raises(VerifyError, match="partial cleanup"):
        inventory(replace(snapshot, replace=True))
    assert _files(out) == before
    assert sorted(p.name for p in out.parent.iterdir()) == ["out", "semantic"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows open-file rename contract")
def test_windows_open_file_refuses_replace_with_exit_two(
    snapshot: InventoryRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = Path(snapshot.output)
    before = _files(out)
    monkeypatch.setattr(reader, "RestSupersetSource", lambda client: Recorded())
    with (out / "sales/weekly@1.yaml").open("rb"):
        assert (
            main(
                [
                    "decision",
                    "inventory",
                    str(snapshot.semantic),
                    "--dashboard",
                    f"{REF}=12",
                    "--replace",
                    "--output",
                    str(out),
                ]
            )
            == 2
        )
    assert _files(out) == before
    assert sorted(p.name for p in out.parent.iterdir()) == ["out", "semantic"]


def test_rest_snapshot_refresh_to_pr_comment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot = tmp_path / "pilot"
    shutil.copytree(PILOT, pilot, ignore=shutil.ignore_patterns("evidence", "dashboards"))
    revenue = pilot / "candidate/metrics/revenue.yaml"
    metric_text = revenue.read_text(encoding="utf-8")
    assert "timezone: Europe/Moscow" in metric_text
    revenue.write_bytes(
        metric_text.replace("timezone: Europe/Moscow", "timezone: UTC").encode("utf-8")
    )
    contract_path = pilot / "contracts/weekly_revenue.yaml"
    contract = yaml.safe_load(contract_path.read_bytes())
    refs = ["dashboard://weekly_revenue@1", "dashboard://refused@1", "dashboard://unmapped@1"]
    contract["delivery"]["dashboards"] = [{"ref": ref} for ref in refs]
    contract_path.write_bytes(yaml.safe_dump(contract).encode("utf-8"))
    mapping = _map(tmp_path, [(refs[0], "12"), (refs[1], "12")])
    out = tmp_path / "out"
    calls: list[tuple[str, str]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path.endswith("/login"):
            return httpx.Response(200, json={"access_token": "offline"})
        if path.endswith("/csrf_token/"):
            return httpx.Response(200, json={"result": "offline"})
        if path == "/api/v1/chart/":
            return httpx.Response(200, json={"result": []})
        pieces = path.split("/")
        if pieces[3] == "dataset":
            # The D6 recording holds no full dataset definitions (as `Recorded` answers).
            return httpx.Response(404, json={"message": "Not found"})
        answers = _answers(pieces[4])
        key = pieces[5] if len(pieces) > 5 else "dashboard"
        return httpx.Response(200, json={"result": answers[key]})

    real_client = SupersetClient

    def client(*args: Any, **kwargs: Any) -> SupersetClient:
        http = httpx.Client(
            base_url="http://superset.test", transport=httpx.MockTransport(transport)
        )
        return real_client("http://superset.test", "reader", "offline", http=http)

    monkeypatch.setattr("auto_bi.adapters.superset.client.SupersetClient", client)
    argv = [
        "decision",
        "inventory",
        str(pilot / "base"),
        "--map",
        str(mapping),
        "--database",
        "2",
        "--output",
        str(out),
    ]
    assert main(argv) == 0
    assert (out / "refused@1.yaml").is_file()
    _map(tmp_path, [(refs[0], "12"), (refs[1], "11")])
    assert main([*argv, "--replace"]) == 3
    assert not (out / "refused@1.yaml").exists()
    report = json.loads((out / "inventory.json").read_bytes())
    assert report["dashboards"][1]["refusals"]
    assert all(method == "GET" or path == "/api/v1/security/login" for method, path in calls)
    assert any(path.endswith("/12/charts") for _, path in calls)
    assert ("GET", "/api/v1/dataset/25") in calls
    capsys.readouterr()

    def no_bi(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("the PR run reached Superset")

    monkeypatch.setattr("auto_bi.adapters.superset.client.SupersetClient", no_bi)
    comment = tmp_path / "comment.md"
    verify_argv = [
        "decision",
        "verify",
        str(pilot / "contracts"),
        "--base",
        str(pilot / "base"),
        "--candidate",
        str(pilot / "candidate"),
        "--product",
        "decision://sales/weekly_revenue",
        "--dashboards",
        str(out),
        "--evidence",
        str(tmp_path / "evidence"),
        "--created-at",
        "2026-08-25T09:00:00Z",
        "--pr-comment",
        str(comment),
        "--json",
    ]
    code = main(verify_argv)
    captured = capsys.readouterr()
    assert code in (3, 4) and captured.err == ""
    gate = json.loads(captured.out)
    text = comment.read_text(encoding="utf-8")
    assert "computes_metric" in text and "chart.sql_partial" in text
    assert "dashboard://weekly_revenue@1#chart_" in text
    assert "bi.spec_missing" in text
    assert all(ref in text for ref in refs[1:])
    assert "dashboard://refused@1#chart_" not in text
    assert "dashboard://unmapped@1#chart_" not in text
    # Inventory metadata/freshness is not a gate input. Same specs -> same verdict/evidence.
    report["dashboards"][0]["changed_on"] = "1900-01-01"
    report["reader_version"] = 999
    (out / "inventory.json").write_bytes(json.dumps(report).encode())
    assert main(verify_argv) == code
    again = capsys.readouterr()
    assert json.loads(again.out) == gate
    assert comment.read_text(encoding="utf-8") == text


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/decisionops-gate"
STAND = Path(__file__).parent / "fixtures/superset_inventory/example_weekly-margin.json"


class Stand:
    """E3 answers without saved charts or definitions; reader 3 retains fallback specs."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = json.loads(STAND.read_bytes())
        self.asked: list[str] = []

    def dashboard(self, ident: str) -> Any:
        self.asked.append(ident)
        return self.answers["dashboard"]

    def charts(self, ident: str) -> Any:
        self.asked.append(ident)
        return self.answers["charts"]

    def datasets(self, ident: str) -> Any:
        self.asked.append(ident)
        return self.answers["datasets"]

    def saves(self, dashboard_id: int) -> list[dict[str, Any]]:
        assert dashboard_id == self.answers["dashboard"]["id"]
        return []

    def chart(self, chart_id: int) -> dict[str, Any]:
        raise AssertionError("the E3 recording has no saved-chart evidence")

    def rendered(self, chart_id: int) -> tuple[int, dict[str, Any]]:
        raise AssertionError("the E3 recording has no saved-chart evidence")

    def dataset_definition(self, dataset_id: int) -> tuple[int, dict[str, Any]]:
        return 404, {}


def test_example_snapshot_is_the_inventory_of_the_stand_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The E3 snapshot remains reader 1 evidence. Reader 3 changes only report metadata for
    # these answers; compare every spec byte and the exact, versioned reason additions.
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    newline = chr(10)
    section = readme.split("## Снимок дашбордов", 1)[1]
    block = section.split("```sh" + newline, 1)[1].split(newline + "```", 1)[0]
    args = shlex.split(block.replace(chr(92) + newline, " "))
    assert args[:5] == ["AUTO_BI_NO_ENV_FILE=1", "uv", "run", "--no-env-file", "auto_bi"]
    root = tmp_path / "examples/decisionops-gate"
    shutil.copytree(EXAMPLE, root)
    monkeypatch.chdir(tmp_path)
    stand = Stand()
    monkeypatch.setattr(reader, "RestSupersetSource", lambda client: stand)
    assert main(args[5:]) == 0
    expected = _files(EXAMPLE / "dashboards")
    report = json.loads(expected["inventory.json"])
    assert report["reader_version"] == 1
    report["reader_version"] = 3
    for chart in report["dashboards"][0]["charts"][1:]:
        chart["reasons"].append("saved: the last change is not a save within two seconds")
        chart["reasons"].append("composite_sql: the dataset definition is not readable")
    actual = _files(root / "dashboards")
    assert json.loads(actual.pop("inventory.json")) == report
    expected.pop("inventory.json")
    assert actual == expected
    assert set(stand.asked) == {"weekly-margin"}
    fresh = [a for a in args[5:] if a != "--replace"]
    fresh[fresh.index("--output") + 1] = str(tmp_path / "fresh")
    assert "--replace" in args and main(fresh) == 0
    assert _files(tmp_path / "fresh") == _files(root / "dashboards")
