"""A contract file whose YAML cannot be built is a finding about the file, not a traceback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from yaml.constructor import ConstructorError

from auto_bi.cli import main
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.semantic.gateway import NativeModelSource, SemanticSnapshot
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import CheckStatus
from auto_bi.yaml_document import load_yaml_document
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    PRODUCT_ID,
    Case,
    _check_payload,
    _write_yaml,
    metric_contract,
    product_contract,
    status_of,
    summary_of,
)

VERIFIED = f"{PRODUCT_ID}@1"
METRIC = "metric://sales/gross_margin_pct"
PIN = f"{METRIC}@3"
UNREAD = "cannot read contract: ConstructorError"
UNREAD_IN_A_SNAPSHOT = "metric contract cannot be read: ConstructorError"

#: A line of YAML that scans, parses and composes, and the class building its value raises:
#: PyYAML 6.0.3 lets every one of them out of `yaml.safe_load` as it is.
UNBUILT = [
    pytest.param("as_of: 2026-02-30\n", ValueError, id="a date that does not exist"),
    pytest.param("as_of: 2026-01-01T00:00:00+99:99\n", ValueError, id="a zone offset past a day"),
    pytest.param(f"sample_size: {'9' * 5000}\n", ValueError, id="an integer of 5000 digits"),
    pytest.param("sample_size: !!int many\n", ValueError, id="an !!int that is not one"),
    pytest.param("share: !!float most\n", ValueError, id="a !!float that is not one"),
    pytest.param("certified: !!bool perhaps\n", KeyError, id="a !!bool that is not one"),
    pytest.param("as_of: !!timestamp lately\n", AttributeError, id="a !!timestamp that is not one"),
    pytest.param(f"tags: {'[' * 3000}{']' * 3000}\n", RecursionError, id="nesting past the stack"),
]
UNBUILT_LINES = [pytest.param(case.values[0], id=str(case.id)) for case in UNBUILT]
NO_SUCH_DATE = "as_of: 2026-02-30\n"

#: Texts PyYAML refuses by itself, and the class it refuses them with.
REFUSED_BY_PYYAML = [
    pytest.param("dimensions: [region, category\n", "ParserError", id="an open sequence"),
    pytest.param("metadata:\n\tid: 5\n", "ScannerError", id="a tab that indents"),
    pytest.param("kind: a\n---\nkind: b\n", "ComposerError", id="two documents"),
    pytest.param("owner: *nobody\n", "ComposerError", id="an alias nothing anchors"),
    pytest.param("formula: !!python/object:os.system {}\n", "ConstructorError", id="a foreign tag"),
    pytest.param("? [region, category]\n: 1\n", "ConstructorError", id="a key that cannot be one"),
    pytest.param("digest: !!binary a\n", "ConstructorError", id="!!binary that is not base64"),
]

READABLE = [
    "",
    "# a comment alone\n",
    "kind: MetricContract\n",
    "- 1\n- two\n- 3.0\n- true\n- null\n",
    "as_of: 2026-02-28\n",
    "as_of: 2026-01-01T00:00:00+03:00\n",
    f"sample_size: {'9' * 4300}\n",
    "elapsed: 1:30:00\n",
    f"tags: {'[' * 40}{']' * 40}\n",
    "certified: !!bool yes\n",
    "share: !!float 1\n",
]


def _unbuilt_contract(line: str) -> bytes:
    """The fixture's metric contract and one more line, whose value cannot be built."""
    text: str = yaml.safe_dump(metric_contract(), allow_unicode=True, sort_keys=False)
    return (text + line).encode("utf-8")


def _unreadable(path: Path) -> dict[str, Any]:
    return {
        "code": "contract.unreadable",
        "severity": "block",
        "subject": str(path),
        "source": str(path),
        "message": UNREAD,
    }


def _unread_gap(name: str, raw: bytes) -> dict[str, Any]:
    return {
        "code": "gap.contract_invalid",
        "severity": "block",
        "path": f"contracts.{name}",
        "reason": UNREAD_IN_A_SNAPSHOT,
        "source_value_ref": digest_bytes(raw),
    }


def _snapshot(root: Path) -> SemanticSnapshot:
    return NativeModelSource(root / "model.yaml", root / "metrics").snapshot()


# --- the loader -----------------------------------------------------------------


@pytest.mark.parametrize(("line", "raised"), UNBUILT)
def test_a_document_that_cannot_be_built_is_a_yaml_error(
    line: str, raised: type[Exception]
) -> None:
    with pytest.raises(raised) as theirs:
        yaml.safe_load(line)
    assert not isinstance(theirs.value, yaml.YAMLError)

    with pytest.raises(yaml.YAMLError) as ours:
        load_yaml_document(line)

    assert type(ours.value) is ConstructorError
    assert type(ours.value.__cause__) is raised
    # The class that was raised and nothing of its message: `invalid literal ... 'many'`
    # quotes the document.
    assert str(ours.value) == f"{raised.__name__} while the document was built"


@pytest.mark.parametrize(("text", "name"), REFUSED_BY_PYYAML)
def test_what_pyyaml_refuses_itself_is_raised_unchanged(text: str, name: str) -> None:
    with pytest.raises(yaml.YAMLError) as theirs:
        yaml.safe_load(text)
    with pytest.raises(yaml.YAMLError) as ours:
        load_yaml_document(text)

    assert type(ours.value) is type(theirs.value)
    assert type(ours.value).__name__ == name
    assert str(ours.value) == str(theirs.value)
    assert ours.value.__cause__ is None


@pytest.mark.parametrize("text", READABLE)
def test_a_document_pyyaml_builds_is_returned_as_it_builds_it(text: str) -> None:
    assert repr(load_yaml_document(text)) == repr(yaml.safe_load(text))


@pytest.mark.parametrize("step", ["get_single_node", "construct_document"])
@pytest.mark.parametrize("raised", [MemoryError, KeyboardInterrupt, SystemExit])
def test_what_says_nothing_about_the_document_is_not_converted(
    monkeypatch: pytest.MonkeyPatch, raised: type[BaseException], step: str
) -> None:
    """Whether it is raised while the document is composed or while it is built."""

    def refuse(*_args: Any) -> Any:
        raise raised

    monkeypatch.setattr(yaml.SafeLoader, step, refuse)

    with pytest.raises(raised):
        load_yaml_document("kind: MetricContract\n")


@pytest.mark.parametrize("step", ["get_single_node", "construct_document"])
def test_what_else_is_raised_at_either_step_is_a_document_that_cannot_be_built(
    monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    def refuse(*_args: Any) -> Any:
        raise LookupError("what the file holds")

    monkeypatch.setattr(yaml.SafeLoader, step, refuse)

    with pytest.raises(yaml.YAMLError) as caught:
        load_yaml_document("kind: MetricContract\n")

    assert type(caught.value) is ConstructorError
    assert type(caught.value.__cause__) is LookupError
    assert str(caught.value) == "LookupError while the document was built"


# --- the validated paths --------------------------------------------------------


@pytest.mark.parametrize("line", UNBUILT_LINES)
def test_a_validated_file_that_cannot_be_built_is_an_unreadable_contract(
    tmp_path: Path, line: str
) -> None:
    case = Case(tmp_path)
    path = case.contracts / "revenue.yaml"
    path.write_bytes(_unbuilt_contract(line))

    report = validate_paths([case.contracts])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [_unreadable(path)]
    assert report.products == [VERIFIED]
    assert report.metrics == [PIN]
    assert report.verdict is Verdict.BLOCK


def test_a_pinned_metric_whose_contract_cannot_be_built_is_unknown_to_its_product(
    tmp_path: Path,
) -> None:
    case = Case(tmp_path)
    path = case.contracts / "gross_margin_pct.yaml"
    path.write_bytes(_unbuilt_contract(NO_SUCH_DATE))

    report = validate_paths([case.contracts])

    assert [issue.model_dump(mode="json") for issue in report.issues] == [
        _unreadable(path),
        {
            "code": "metric_ref.unknown",
            "severity": "block",
            "subject": PIN,
            "source": VERIFIED,
            "message": "no metric contract with this id in the validated set",
        },
    ]
    assert report.products == [VERIFIED]
    assert report.metrics == []


@pytest.mark.parametrize("line", UNBUILT_LINES)
def test_decision_validate_names_the_file_and_exits_by_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], line: str
) -> None:
    case = Case(tmp_path)
    path = case.contracts / "revenue.yaml"
    path.write_bytes(_unbuilt_contract(line))

    assert main(["decision", "validate", str(case.contracts)]) == 4
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == [
        f"{'block':<8} {'contract.unreadable':<26} {path}",
        f"{'':<8} {UNREAD} [{path}]",
    ]
    assert printed[-1] == "verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))"

    assert main(["decision", "validate", str(case.contracts), "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["issues"] == [_unreadable(path)]


# --- a contract that is a mapping and whose metadata is not ---------------------


@pytest.mark.parametrize("kind", ["metric", "product"])
@pytest.mark.parametrize("metadata", [5, 2.5, True, "sales", ["id", "version"], None], ids=repr)
def test_a_contract_whose_metadata_is_not_a_mapping_is_invalid_under_its_file_name(
    tmp_path: Path, kind: str, metadata: object
) -> None:
    case = Case(tmp_path)
    contract = metric_contract() if kind == "metric" else product_contract()
    contract["metadata"] = metadata
    path = case.contracts / "extra.yaml"
    _write_yaml(path, contract)

    report = validate_paths([case.contracts])

    [issue] = report.issues
    assert (issue.code, issue.severity.value) == ("contract.invalid", "block")
    assert (issue.subject, issue.source) == ("extra.yaml", str(path))
    assert issue.message.startswith("metadata: ")
    assert report.products == [VERIFIED]
    assert report.metrics == [PIN]
    assert main(["decision", "validate", str(case.contracts)]) == 4


@pytest.mark.parametrize(
    ("metadata", "subject"),
    [
        pytest.param({"id": "metric://sales/revenue"}, "metric://sales/revenue", id="an id"),
        pytest.param({"version": 3}, "extra.yaml", id="a mapping without an id"),
        pytest.param({}, "extra.yaml", id="an empty mapping"),
    ],
)
def test_an_invalid_contract_is_named_by_the_id_it_declares_or_by_its_file(
    tmp_path: Path, metadata: dict[str, Any], subject: str
) -> None:
    case = Case(tmp_path)
    contract = metric_contract()
    contract["metadata"] = metadata
    path = case.contracts / "extra.yaml"
    _write_yaml(path, contract)

    [issue] = validate_paths([case.contracts]).issues

    assert (issue.code, issue.subject, issue.source) == ("contract.invalid", subject, str(path))


def test_an_invalid_contract_without_metadata_is_named_by_its_file(tmp_path: Path) -> None:
    case = Case(tmp_path)
    contract = metric_contract()
    del contract["metadata"]
    path = case.contracts / "extra.yaml"
    _write_yaml(path, contract)

    [issue] = validate_paths([case.contracts]).issues

    assert (issue.code, issue.subject, issue.source) == (
        "contract.invalid",
        "extra.yaml",
        str(path),
    )


# --- the semantic refs ----------------------------------------------------------


@pytest.mark.parametrize("line", UNBUILT_LINES)
def test_a_snapshot_names_a_contract_that_cannot_be_built_as_a_gap(
    tmp_path: Path, line: str
) -> None:
    case = Case(tmp_path)
    clean = _snapshot(case.base)
    raw = _unbuilt_contract(line)
    (case.base / "metrics" / "revenue.yaml").write_bytes(raw)

    snapshot = _snapshot(case.base)

    assert clean.gaps == []
    assert [gap.model_dump(mode="json") for gap in snapshot.gaps] == [
        _unread_gap("revenue.yaml", raw)
    ]
    # The file is part of what the snapshot was read from and no part of what it holds.
    assert [node.id for node in snapshot.graph.metrics] == [METRIC]
    assert snapshot.canonical_digest == clean.canonical_digest
    assert snapshot.source_digest != clean.source_digest


# --- the gate -------------------------------------------------------------------


def test_the_gate_blocks_on_a_validated_contract_that_cannot_be_built(tmp_path: Path) -> None:
    case = Case(tmp_path)
    path = case.contracts / "gross_margin_pct.yaml"
    path.write_bytes(_unbuilt_contract(NO_SUCH_DATE))

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "contracts.validate") is CheckStatus.BLOCK
    assert summary_of(report, "contracts.validate") == "contracts.issues"
    issues = _check_payload(tmp_path, report, "contracts.validate")["issues"]
    assert issues[0] == _unreadable(path)
    assert [issue["code"] for issue in issues] == ["contract.unreadable", "metric_ref.unknown"]


@pytest.mark.parametrize(("side", "other"), [("base", "candidate"), ("candidate", "base")])
def test_the_gate_blocks_on_a_snapshot_contract_that_cannot_be_built(
    tmp_path: Path, side: str, other: str
) -> None:
    case = Case(tmp_path).with_candidate()
    raw = _unbuilt_contract(NO_SUCH_DATE)
    (tmp_path / side / "metrics" / "gross_margin_pct.yaml").write_bytes(raw)

    report = case.run()

    assert report.verdict is Verdict.BLOCK
    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    assert summary_of(report, "semantic.gaps") == "semantic.gaps_present"
    payload = _check_payload(tmp_path, report, "semantic.gaps")
    assert payload[side] == [_unread_gap("gross_margin_pct.yaml", raw)]
    assert payload[other] == []


@pytest.mark.parametrize("where", ["contracts", "base/metrics", "candidate/metrics"])
@pytest.mark.parametrize("line", UNBUILT_LINES)
def test_decision_verify_exits_by_the_verdict_and_writes_the_bundle(
    tmp_path: Path, where: str, line: str
) -> None:
    case = Case(tmp_path).with_candidate()
    (tmp_path / where / "gross_margin_pct.yaml").write_bytes(_unbuilt_contract(line))

    assert main(_verify_argv(case)) == 4

    [run] = (tmp_path / "evidence").iterdir()
    assert (run / "manifest.json").is_file()
