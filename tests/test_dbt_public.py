"""F4 of ADR 0010: the formulas of public dbt projects, read.

`scripts/dbt_corpus.py public` parsed, on the Mac stand, three public dbt-labs projects at pinned
revisions — `dbt deps` and `dbt parse` only, in a temporary copy, no database reached, no secret
in the environment — and recorded for each the `require-dbt-version` it declares, and dbt's
refusal, or the reader's problems and what the resolver answers for each metric against a
contract on `dm.public_grain`, no table of the project. dbt-core 1.12.6 refuses
jaffle-sl-template as written; it is parsed again with the one behaviour flag its cumulative
metric needs. The artifacts stay on the stand — neither jaffle repository carries a licence — and
`tests/fixtures/dbt_semantic_manifest/public` keeps the records. These tests hold the records to
the script and to the inventory: every gap a project gives, not one the contract decides, is one
row of `docs/specs/dbt-formula-inventory.md`, told by the resolver's reason; and they hold the
table of the report to the records.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from auto_bi.evidence.canonical import digest_bytes
from tests.test_dbt_corpus import CORPUS, RAW, RESULT, SCRIPT, TELLS, UNNESTED, _group, _triple
from tests.test_metricflow_formula import FIXTURES, REPO

PUBLIC = FIXTURES / "public"
REPORT = REPO / "docs" / "reviews" / "DECISIONOPS_DBT_PUBLIC_2026-11-10.md"
RECORDS: dict[str, dict[str, Any]] = {
    path.stem: json.loads(path.read_bytes()) for path in sorted(PUBLIC.glob("*.json"))
}
TEMPLATE = "github.com/dbt-labs/jaffle-sl-template@c54c8fb8bea86c0c4d3d5c588d55f8de9de57ade"
SOURCES = {
    "jaffle_shop": "github.com/dbt-labs/jaffle-shop@5beb145b00f5465ec759cfcdd9745e858818cf95",
    "jaffle_sl_template": TEMPLATE,
    "jaffle_sl_template_unnested": TEMPLATE,
    "metricflow_tutorial": "github.com/dbt-labs/metricflow@fe4b8c1ccb72a8ca77494b345777eff20a714119"
    " dbt-metricflow/dbt_metricflow/cli (mf_tutorial_project, sample_dbt_models)",
}
#: The `require-dbt-version` each project declares: jaffle-shop's, of dbt 2, is not met by the
#: dbt-core of the corpus, which reads it with the version check off.
REQUIRES = {
    "jaffle_shop": ">=2.0.0",
    "jaffle_sl_template": ">=1.6.0rc2",
    "jaffle_sl_template_unnested": ">=1.6.0rc2",
    "metricflow_tutorial": None,
}
#: The records dbt wrote an artifact for.
READ = {name: record for name, record in RECORDS.items() if "refused" not in record}
#: The rows a contract on `dm.public_grain` decides, not the project: the grain table and the
#: event time column are the contract's. Every other row is the project's — parity too, which a
#: metric of another table does not reach.
CONTRACT_ROWS = frozenset({"relation.table", "time.column-name", "time.column-expr"})
#: The rows `TELLS` tells apart by the artifact's value at the gap's path, not by the reason — a
#: time dimension's `expr` and its name, the keys of `agg_params`. Without the artifact, a gap
#: of theirs is its group.
BY_VALUE = frozenset(
    {
        "time.expression",
        "time.name",
        *(
            f"{dialect}.agg_params.{key}"
            for dialect in ("legacy", "v2")
            for key in ("percentile", "use_discrete_percentile", "use_approximate_percentile")
        ),
    }
)
_TABLE_ROW = re.compile(
    r"^\| `(?P<name>[a-z_]+)` \| (?P<metrics>\d+) \| (?P<free>\d+) \| (?P<rows>.*) \|$"
)


def _rows(gap: dict[str, str]) -> frozenset[str]:
    """The rows of the inventory a gap is: of the rows with its code, severity and path shape,
    those its reason tells — or, for rows only the artifact tells apart, all of them."""
    group = frozenset(_group(_triple(gap)))
    if len(group) < 2 or group <= BY_VALUE:
        return group
    assert not group & BY_VALUE, group
    return frozenset(ident for ident in group if TELLS[ident](gap["reason"], None))


def _own(gaps: list[dict[str, str]]) -> list[dict[str, str]]:
    """The gaps of the project: not those the contract on `dm.public_grain` decides."""
    return [gap for gap in gaps if not _rows(gap) or not _rows(gap) <= CONTRACT_ROWS]


def _rows_hit(outcomes: dict[str, Any]) -> Counter[str]:
    """For each row of the inventory a gap of the project is, the metrics with such a gap."""
    hit: Counter[str] = Counter()
    for found in outcomes.values():
        hit.update({" / ".join(sorted(_rows(gap))) for gap in _own(found.get("gaps", []))})
    return hit


def test_the_records_are_of_the_pinned_projects() -> None:
    assert {name: record["source"] for name, record in RECORDS.items()} == SOURCES
    assert {name: record["require_dbt_version"] for name, record in RECORDS.items()} == REQUIRES


def test_a_metric_free_of_project_gaps_stops_at_the_contracts_table() -> None:
    """A metric free of the project's gaps is stopped at the table: the contract's, not its own."""
    free = 0
    for name, record in READ.items():
        for metric, found in record["outcomes"].items():
            if not _own(found["gaps"]):
                free += 1
                assert "relation.table" in {i for gap in found["gaps"] for i in _rows(gap)}, (
                    name,
                    metric,
                )
    assert free == 4


def test_the_records_are_of_the_versions_of_the_corpus() -> None:
    versions = json.loads((CORPUS / "result.json").read_bytes())["versions"]
    assert {name: record["versions"] for name, record in RECORDS.items()} == dict.fromkeys(
        RECORDS, versions
    )


def test_dbt_refuses_the_template_as_written_for_its_unnested_cumulative_metric() -> None:
    written = RECORDS["jaffle_sl_template"]
    assert written["flags"] == {}
    assert sorted(written) == ["flags", "refused", "require_dbt_version", "source", "versions"]
    assert "Semantic Manifest validation failed" in written["refused"]
    assert re.findall(r"Invalid metrics: ([^.]*)\.", written["refused"]) == ["orders_last_7_days"]
    # the one flag set anywhere, and the corpus's own for an unnested cumulative metric
    assert {name: record["flags"] for name, record in RECORDS.items() if record["flags"]} == {
        "jaffle_sl_template_unnested": UNNESTED
    }
    assert "orders_last_7_days" in READ["jaffle_sl_template_unnested"]["outcomes"]


def test_the_reader_reads_each_artifact_dbt_wrote() -> None:
    assert sorted(READ) == ["jaffle_shop", "jaffle_sl_template_unnested", "metricflow_tutorial"]
    for name, record in READ.items():
        assert record["problems"] == [], name
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", record["artifact"]), name
        assert record["outcomes"], name


def test_each_gap_of_a_project_is_one_row_of_the_inventory() -> None:
    """One row, or — where only the artifact tells its rows apart — one group of them."""
    untold = [
        (name, metric, gap)
        for name, record in READ.items()
        for metric, found in record["outcomes"].items()
        for gap in _own(found.get("gaps", []))
        if not (len(_rows(gap)) == 1 or (_rows(gap) and _rows(gap) <= BY_VALUE))
    ]
    assert untold == []


def test_a_project_stops_a_metric_only_for_review() -> None:
    """The project's gaps are constructs outside the subset and a time the resolver cannot
    prove; a block comes only from the contract on a table of no project."""
    own = {
        (gap["code"], gap["severity"])
        for record in READ.values()
        for found in record["outcomes"].values()
        for gap in _own(found.get("gaps", []))
    }
    assert own == {
        ("gap.external_construct_unsupported", "review"),
        ("gap.external_time_unproven", "review"),
    }


def test_each_metric_resolves_or_has_a_gap() -> None:
    for name, record in READ.items():
        for metric, found in record["outcomes"].items():
            assert ("formula" in found) != bool(found.get("gaps")), (name, metric)


def test_the_report_table_is_the_records() -> None:
    table: dict[str, tuple[int, int, str]] = {}
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        match = _TABLE_ROW.match(line)
        if match:
            table[match["name"]] = (int(match["metrics"]), int(match["free"]), match["rows"])
    expected = {}
    for name, record in READ.items():
        outcomes = record["outcomes"]
        free = sum(1 for found in outcomes.values() if not _own(found.get("gaps", [])))
        hit = sorted(_rows_hit(outcomes).items())
        rows = "; ".join(f"`{told}` {count}" for told, count in hit)
        expected[name] = (len(outcomes), free, rows)
    assert table == expected


def test_the_report_names_each_metric_only_the_contract_stops() -> None:
    text = REPORT.read_text(encoding="utf-8")
    free = [
        metric
        for record in READ.values()
        for metric, found in record["outcomes"].items()
        if not _own(found.get("gaps", []))
    ]
    assert len(free) == 4
    assert [metric for metric in free if f"`{metric}`" not in text] == []
    total = sum(len(record["outcomes"]) for record in READ.values())
    assert f"Из {total} метрик гэпов проекта нет у четырёх" in text
    assert f"Остальные {total - len(free)}" in text


def test_no_v2_row_of_the_parity_matrix_is_proven() -> None:
    """Why the report says the V2 metrics free of project gaps would stop at `parity.v2` on a
    table of their own: the corpus's proven rows are legacy rows only."""
    assert SCRIPT.PROVEN_PARITY
    assert [row for row in SCRIPT.PROVEN_PARITY if "v2." in row] == []


# --- the script's `public`, with dbt answering as told ------------------------------------

PROJECT_YML = "name: shop\nprofile: shop\n"


def _project(root: Path, *, packages: bool = True, settings: str = PROJECT_YML) -> Path:
    project = root / "shop"
    (project / "models").mkdir(parents=True)
    (project / "dbt_project.yml").write_text(settings, encoding="utf-8", newline="\n")
    (project / "models" / "orders.sql").write_text("select 1\n", encoding="utf-8", newline="\n")
    if packages:
        (project / "packages.yml").write_text("packages: []\n", encoding="utf-8", newline="\n")
    return project


def _dbt_answering(
    monkeypatch: pytest.MonkeyPatch,
    *,
    refuse: str | None = None,
    writes: bytes | None = RAW["legacy"],
) -> list[dict[str, Any]]:
    """Have the script's dbt answer: `deps` with nothing, `parse` with `writes` (the legacy
    corpus; `None`, nothing) where it is told, or with `refuse` on stdout and exit 1. Each call,
    with what it saw."""
    calls: list[dict[str, Any]] = []

    def dbt(_dbt: str, args: Sequence[str], home: Path) -> subprocess.CompletedProcess[str]:
        command, rest = args[0], list(args[1:])
        work = Path(rest[rest.index("--project-dir") + 1])
        profiles = Path(rest[rest.index("--profiles-dir") + 1])
        calls.append(
            {
                "command": command,
                "home": home,
                "work": work,
                "profile": rest[rest.index("--profile") + 1],
                "profiles": (profiles / "profiles.yml").read_text(encoding="utf-8"),
                "settings": (work / "dbt_project.yml").read_text(encoding="utf-8"),
                "files": sorted(
                    path.relative_to(work).as_posix() for path in work.rglob("*") if path.is_file()
                ),
            }
        )
        if command == "parse":
            if refuse is not None:
                return subprocess.CompletedProcess(args, 1, f"{work}\n{refuse}\n", "")
            target = Path(rest[rest.index("--target-path") + 1])
            target.mkdir(parents=True)
            if writes is not None:
                (target / "semantic_manifest.json").write_bytes(writes)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(SCRIPT, "_dbt", dbt)
    monkeypatch.setattr(SCRIPT, "_versions", lambda _dbt: RESULT["versions"])
    return calls


def test_public_parses_a_copy_with_its_flags_and_records_the_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    project = _project(tmp_path)
    before = sorted(path.relative_to(project) for path in project.rglob("*"))
    out, artifact = tmp_path / "out" / "shop.json", tmp_path / "kept" / "semantic_manifest.json"

    assert (
        SCRIPT.public("dbt", project, out, source="here@1", flags=UNNESTED, artifact=artifact) == 0
    )

    assert [call["command"] for call in calls] == ["deps", "parse"]
    for call in calls:
        assert call["work"] == call["home"] / "project" != project
        assert call["profile"] == SCRIPT._PROFILE
        assert call["profiles"] == SCRIPT.project_files("legacy", [])["profiles.yml"]
        assert (
            call["settings"]
            == PROJECT_YML + "flags:\n  require_nested_cumulative_type_params: false\n"
        )
    # the project itself gets nothing written, and the copy is gone
    assert sorted(path.relative_to(project) for path in project.rglob("*")) == before
    assert (project / "dbt_project.yml").read_text(encoding="utf-8") == PROJECT_YML
    assert not calls[0]["home"].exists()
    assert artifact.read_bytes() == RAW["legacy"]
    record = json.loads(out.read_bytes())
    read = SCRIPT.read_semantic_manifest(RAW["legacy"])
    names = sorted(metric["name"] for metric in json.loads(RAW["legacy"])["metrics"])
    assert record == {
        "source": "here@1",
        "flags": UNNESTED,
        "require_dbt_version": None,
        "versions": RESULT["versions"],
        "artifact": digest_bytes(RAW["legacy"]),
        "problems": [],
        "outcomes": {name: SCRIPT.outcome(read, name, SCRIPT.PUBLIC_TABLE) for name in names},
    }


def test_public_fetches_no_packages_a_project_has_none_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    out = tmp_path / "shop.json"
    assert SCRIPT.public("dbt", _project(tmp_path, packages=False), out, source="here@1") == 0
    assert [call["command"] for call in calls] == ["parse"]
    assert calls[0]["settings"] == PROJECT_YML
    assert json.loads(out.read_bytes())["flags"] == {}


def test_public_writes_and_records_the_flags_in_one_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    out = tmp_path / "shop.json"
    project = _project(tmp_path, packages=False)
    assert SCRIPT.public("dbt", project, out, source="here@1", flags={"b": 3, "a": False}) == 0
    assert calls[0]["settings"] == PROJECT_YML + "flags:\n  a: false\n  b: 3\n"
    assert list(json.loads(out.read_bytes())["flags"]) == ["a", "b"]


def test_public_records_the_readers_problems_and_no_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = json.loads(RAW["legacy"])
    del broken["metrics"]
    raw = json.dumps(broken).encode()
    _dbt_answering(monkeypatch, writes=raw)
    out = tmp_path / "shop.json"
    assert SCRIPT.public("dbt", _project(tmp_path), out, source="here@1") == 0
    problems = SCRIPT.read_semantic_manifest(raw).problems
    assert problems
    assert json.loads(out.read_bytes()) == {
        "source": "here@1",
        "flags": {},
        "require_dbt_version": None,
        "versions": RESULT["versions"],
        "artifact": digest_bytes(raw),
        "problems": [{"code": p.code, "path": p.path, "reason": p.reason} for p in problems],
    }


def test_public_records_dbts_refusal_and_keeps_no_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dbt_answering(monkeypatch, refuse="[ERROR]: Semantic Manifest validation failed.")
    out, artifact = tmp_path / "shop.json", tmp_path / "kept.json"
    project = _project(tmp_path)
    assert SCRIPT.public("dbt", project, out, source="here@1", artifact=artifact) == 0
    assert json.loads(out.read_bytes()) == {
        "source": "here@1",
        "flags": {},
        "require_dbt_version": None,
        "versions": RESULT["versions"],
        "refused": "[ERROR]: Semantic Manifest validation failed.",
    }
    assert not artifact.exists()


@pytest.mark.parametrize(
    "own",
    [
        "flags:\n  send_anonymous_usage_stats: false\n",
        "flags: {send_anonymous_usage_stats: false}\n",
        '"flags":\n  send_anonymous_usage_stats: false\n',
    ],
)
def test_public_refuses_to_set_flags_over_the_projects_own(
    own: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    project = _project(tmp_path, settings=PROJECT_YML + own)
    out = tmp_path / "shop.json"
    with pytest.raises(SystemExit, match="sets behaviour flags of its own"):
        SCRIPT.public("dbt", project, out, source="here@1", flags=UNNESTED)
    assert calls == []
    assert not out.exists()


def test_public_records_the_dbt_version_the_project_requires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dbt_answering(monkeypatch)
    out = tmp_path / "shop.json"
    project = _project(tmp_path, settings=PROJECT_YML + 'require-dbt-version: ">=2.0.0"\n')
    assert SCRIPT.public("dbt", project, out, source="here@1") == 0
    assert json.loads(out.read_bytes())["require_dbt_version"] == ">=2.0.0"


def test_public_leaves_out_what_dbt_writes_in_the_root_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    project = _project(tmp_path, packages=False)
    for place in ("logs/dbt.log", "target/run.json", "models/logs/events.sql"):
        (project / place).parent.mkdir(parents=True, exist_ok=True)
        (project / place).write_text("select 1\n", encoding="utf-8", newline="\n")
    assert SCRIPT.public("dbt", project, tmp_path / "shop.json", source="here@1") == 0
    assert calls[0]["files"] == ["dbt_project.yml", "models/logs/events.sql", "models/orders.sql"]


@pytest.mark.parametrize("place", ["../outside", "/outside"])
def test_public_adds_a_directory_only_inside_the_copy(
    place: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _dbt_answering(monkeypatch)
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "seed.csv").write_text("a\n1\n", encoding="utf-8", newline="\n")
    out = tmp_path / "shop.json"
    with pytest.raises(SystemExit, match="is not a place in the project"):
        SCRIPT.public("dbt", _project(tmp_path), out, source="here@1", adds=[(extra, place)])
    assert calls == []
    assert not out.exists()


def test_public_stops_on_a_parse_that_writes_no_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _dbt_answering(monkeypatch, writes=None)
    out = tmp_path / "shop.json"
    with pytest.raises(SystemExit, match="exits 0 and writes no artifact"):
        SCRIPT.public("dbt", _project(tmp_path), out, source="here@1")
    assert not out.exists()


def test_a_flag_is_a_name_and_a_json_value() -> None:
    assert SCRIPT._flag("require_nested_cumulative_type_params=false") == (
        "require_nested_cumulative_type_params",
        False,
    )
    for text in ("no_value", "=false", "name=", "name=nope"):
        with pytest.raises(argparse.ArgumentTypeError):
            SCRIPT._flag(text)


def test_main_passes_each_flag_to_public(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def public(dbt: str, project: Path, out: Path, **keys: Any) -> int:
        seen.update(keys, dbt=dbt, project=project, out=out)
        return 0

    monkeypatch.setattr(SCRIPT, "public", public)
    argv = ["public", "--dbt", "d", "--project", "p", "--source", "s@1", "--out", "o"]
    assert SCRIPT.main([*argv, "--flag", "a=false", "--flag", "b=3", "--add", "x=y"]) == 0
    assert seen == {
        "dbt": "d",
        "project": Path("p"),
        "out": Path("o"),
        "source": "s@1",
        "adds": [(Path("x"), "y")],
        "flags": {"a": False, "b": 3},
        "artifact": None,
    }


def test_main_refuses_a_flag_set_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SCRIPT, "public", lambda *_args, **_keys: pytest.fail("ran"))
    argv = ["public", "--dbt", "d", "--project", "p", "--source", "s@1", "--out", "o"]
    with pytest.raises(SystemExit) as stopped:
        SCRIPT.main([*argv, "--flag", "a=false", "--flag", "a=true"])
    assert stopped.value.code == 2
