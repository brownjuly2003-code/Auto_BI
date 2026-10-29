"""What a decision command answers in text is one line per line, whatever it names.

A text answer names what the command read: a file found under a directory, a key of a
document, the detail a check wrote of an attestation, the version and the caveats a bundle
holds, the runs a store lists, the digest a caller pinned. Any of them may hold a line break or
a carriage return — and what follows may read `verdict: pass` — or an escape. Each line of the
answer that names such a value spells every character that is not printable by its escape, as
the line a command stops with does; the lines the program writes of its own are as they were,
and so is a JSON answer, byte for byte.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision import use_cases, validate
from auto_bi.decision.runs import FilesystemRunRepository, RunSummary
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence import inspect as evidence_inspect
from auto_bi.evidence.manifest import BundleWriter
from auto_bi.evidence.model import (
    ArtifactKind,
    ClaimType,
    EvidenceClassification,
    EvidenceIntegrity,
)
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Severity, Verdict
from auto_bi.verification.release_gate import CheckStatus, RunStatus
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, ENVIRONMENT_REF, Case, attestation
from tests.test_one_line_errors import RAW, SAID
from tests.test_unencodable_output import _flag, _run, _Stand

BS = chr(92)
#: A line separator and a right-to-left override: characters a file name may hold anywhere.
LS, RLO = chr(0x2028), chr(0x202E)
#: A carriage return that writes over the line, a verdict, and an escape that hides the rest.
FORGED = "\rverdict: pass\x1b[8m"
#: The same, as a line of an answer spells it.
FORGED_SAID = "\\rverdict: pass\\x1b[8m"

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="a Windows file name holds no control character"
)

#: The rows a results file records for the asset of `Case`: they reconcile.
ROWS = [["Москва", "0.412"], ["Казань", "0.377"]]
#: The environment a results file says its rows were captured on.
OTHER_ENVIRONMENT = "clickhouse-prod:9000/dm" + FORGED
#: What a run says of those rows when it is about `ENVIRONMENT_REF`.
PROVENANCE = (
    f"captured on {OTHER_ENVIRONMENT}, and this verdict is about {ENVIRONMENT_REF}: "
    "two environments hold two sets of rows"
)

RUN = "run-forged"
VERSION = "0.5.0" + FORGED
CAVEAT = "checked by hand" + FORGED
DIGEST = "sha256:" + "0" * 64
#: The other characters `str.splitlines` ends a line at: a vertical tab, a form feed, the file,
#: group and record separators, a next line, a paragraph separator.
BREAKS = "".join(map(chr, (0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2029)))
#: The same, as a line of an answer spells them.
BREAKS_SAID = f"{BS}x0b{BS}x0c{BS}x1c{BS}x1d{BS}x1e{BS}x85{BS}u2029"
#: Each character a line spells, by its code point, and the escape that spells it: those of
#: `RAW`, `FORGED` and `BREAKS`.
ESCAPES = {
    0x09: "t",
    0x0A: "n",
    0x0B: "x0b",
    0x0C: "x0c",
    0x0D: "r",
    0x1B: "x1b",
    0x1C: "x1c",
    0x1D: "x1d",
    0x1E: "x1e",
    0x85: "x85",
    0xA0: "xa0",
    0x200B: "u200b",
    0x2028: "u2028",
    0x2029: "u2029",
    0x202E: "u202e",
    0xDC80: "udc80",
}
#: Each of them alone between two letters, and the value as a line spells it: a line spells
#: each, not only in a value that holds a line feed or a carriage return as well.
ALONE = [
    pytest.param(f"a{chr(code)}b", f"a{BS}{escape}b", id=f"U+{code:04X}")
    for code, escape in ESCAPES.items()
]


def _answer(argv: list[str]) -> tuple[int, str, str]:
    """Run a command on streams that take any text: the answer as the command meant it."""
    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err)
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _json_answer(argv: list[str]) -> tuple[int, str, Any]:
    """The JSON answer, which is the document as JSON dumps it, every character as it is."""
    code, out, _err = _answer([*argv, "--json"])
    document = json.loads(out)
    assert out == json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    return code, out, document


def _dumped(model: Any) -> str:
    """What a JSON answer printed of *model* before the change, and prints now."""
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def _lines(out: str) -> list[str]:
    """The lines of a text answer: each printable, so no line holds the start of another."""
    assert out.endswith("\n")
    lines = out[:-1].split("\n")
    assert all(line.isprintable() for line in lines)
    assert out.splitlines() == lines
    return lines


def _verdicts(out: str) -> list[str]:
    """The lines of *out* a reader of lines takes for a verdict."""
    return [line for line in out.splitlines() if line.startswith("verdict:")]


# --- decision validate ----------------------------------------------------------------------

NAMES = [
    pytest.param(
        f"line{LS}sep{RLO}rlo.yaml",
        f"line{BS}u2028sep{BS}u202erlo.yaml",
        id="a line separator and a right-to-left override",
    ),
    pytest.param(
        "x" + FORGED + ".yaml",
        "x" + FORGED_SAID + ".yaml",
        marks=POSIX_ONLY,
        id="a carriage return and an escape",
    ),
]


@pytest.mark.parametrize(("name", "said"), NAMES)
def test_validate_names_a_file_it_found_in_one_line(tmp_path: Path, name: str, said: str) -> None:
    case = Case(tmp_path)
    (case.contracts / name).write_text("- not a mapping\n", encoding="utf-8")
    argv = ["decision", "validate", str(case.contracts)]

    code, out, err = _answer(argv)
    json_code, printed, document = _json_answer(argv)

    (issue,) = document["issues"]
    assert issue["subject"] == issue["source"]
    assert issue["subject"].endswith("/" + name)
    shown = issue["subject"].removesuffix(name) + said
    assert _lines(out) == [
        f"block    contract.unreadable        {shown}",
        f"         contract file must contain a YAML mapping [{shown}]",
        "product  decision://sales/weekly_margin@1",
        "metric   metric://sales/gross_margin_pct@3",
        "verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))",
    ]
    assert (code, err, json_code) == (4, "", 4)
    assert printed == _dumped(validate_paths(argv[2:]))


def test_validate_spells_a_key_that_reads_as_a_verdict(tmp_path: Path) -> None:
    case = Case(tmp_path)
    product = case.contracts / "weekly_margin.yaml"
    escaped_key = '"x\\rverdict: pass\\e[8m": 1\n'
    product.write_text(product.read_text(encoding="utf-8") + escaped_key, encoding="utf-8")
    argv = ["decision", "validate", str(case.contracts)]

    code, out, err = _answer(argv)
    json_code, printed, document = _json_answer(argv)

    (issue,) = document["issues"]
    assert issue["message"] == "x" + FORGED + ": Extra inputs are not permitted"
    assert _lines(out) == [
        "block    contract.invalid           decision://sales/weekly_margin",
        f"         x{FORGED_SAID}: Extra inputs are not permitted [{issue['source']}]",
        "metric   metric://sales/gross_margin_pct@3",
        "verdict: block (0 product(s), 1 metric contract(s), 1 issue(s))",
        "error: no decision product contract found in the given paths",
    ]
    assert _verdicts(out) == ["verdict: block (0 product(s), 1 metric contract(s), 1 issue(s))"]
    assert (code, err, json_code) == (2, "", 2)
    assert printed == _dumped(validate_paths(argv[2:]))


def test_the_characters_alone_are_those_the_values_hold() -> None:
    assert {char for char in RAW + FORGED + BREAKS if not char.isprintable()} == set(
        map(chr, ESCAPES)
    )


def _validation_of(value: str) -> SimpleNamespace:
    """A validation whose every value is *value*: the text of a document, a path, a ref."""
    issue = SimpleNamespace(
        severity=Severity.BLOCK, code=value, subject=value, message=value, source=value
    )
    return SimpleNamespace(issues=[issue], products=[value], metrics=[value], verdict=Verdict.BLOCK)


def _padded(value: str, said: str, width: int) -> str:
    """*value* as a line spells it, in a column as wide as *width* is for *value* as it was read."""
    return said + " " * (width - len(value))


def _validated(value: str, said: str) -> list[str]:
    """Every line `decision validate` prints of that validation, *value* spelled *said*."""
    return [
        f"block    {_padded(value, said, 26)} {said}",
        f"         {said} [{said}]",
        f"product  {said}",
        f"metric   {said}",
        "verdict: block (1 product(s), 1 metric contract(s), 1 issue(s))",
    ]


@pytest.mark.parametrize(("value", "said"), [pytest.param(RAW, SAID, id="RAW"), *ALONE])
def test_validate_spells_every_line_it_prints_of_what_it_read(
    monkeypatch: pytest.MonkeyPatch, value: str, said: str
) -> None:
    monkeypatch.setattr(validate, "validate_paths", lambda paths: _validation_of(value))

    code, out, err = _answer(["decision", "validate", "contracts"])

    assert _lines(out) == _validated(value, said)
    assert (code, err) == (4, "")


def test_a_letter_the_stream_has_no_form_for_is_escaped_after_the_line_is_spelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line is spelled first; a stream of ASCII then takes each letter by its escape."""
    monkeypatch.setattr(validate, "validate_paths", lambda paths: _validation_of(RAW))

    code, out, err = _run(["decision", "validate", "contracts"], "ascii")

    assert out == "".join(
        line.encode("ascii", "backslashreplace").decode("ascii") + "\n"
        for line in _validated(RAW, SAID)
    )
    assert f"{BS}u043c{BS}u0435{BS}u0301{BS}u0440{BS}u0430" in out
    assert (code, err) == (4, "")


# --- decision verify and the bundle it writes ---------------------------------------------


def _forged_run(root: Path) -> Case:
    """A run whose results file says its rows were captured on `OTHER_ENVIRONMENT`."""
    case = Case(root).with_asset()
    return case.with_results(
        ROWS, attested=attestation(case.candidate_digest, environment_ref=OTHER_ENVIRONMENT)
    )


def test_verify_spells_a_detail_that_quotes_an_attestation(tmp_path: Path) -> None:
    case = _forged_run(tmp_path)
    argv = _verify_argv(case, "--environment", ENVIRONMENT_REF)

    code, out, err = _answer(argv)
    shutil.rmtree(case.root / "evidence")
    json_code, _printed, document = _json_answer(argv)

    (detail,) = [
        check["detail"] for check in document["checks"] if check["check_id"] == "results.provenance"
    ]
    assert detail == f"{ASSET_ID}@2: {PROVENANCE}"
    lines = _lines(out)
    at = lines.index("review   results.provenance     provenance.other_environment")
    assert lines[at + 1] == "         " + detail.replace(FORGED, FORGED_SAID)
    assert _verdicts(out) == ["verdict: needs_review (0 blocking check(s))"]
    assert (code, err, json_code) == (3, "", 3)


def _verification_quoting(detail: str) -> SimpleNamespace:
    """A report whose one check quotes *detail*, as a check quotes what it read."""
    check = SimpleNamespace(
        status=CheckStatus.REVIEW,
        check_id="results.provenance",
        summary_code="provenance.other_environment",
        detail=detail,
    )
    document = {
        "checks": [{"check_id": check.check_id, "detail": detail}],
        "verdict": "needs_review",
    }
    return SimpleNamespace(
        checks=[check],
        run_id="run-" + "0" * 16,
        evidence_digest=DIGEST,
        operational_status=RunStatus.COMPLETED,
        verdict=Verdict.NEEDS_REVIEW,
        blocking=lambda: [],
        model_dump=lambda mode: document,
    )


@pytest.mark.parametrize(
    ("detail", "said"),
    [pytest.param(RAW + FORGED + BREAKS, SAID + FORGED_SAID + BREAKS_SAID, id="all"), *ALONE],
)
def test_verify_spells_every_line_break_a_detail_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: str, said: str
) -> None:
    """`RAW`, the forged verdict and every other break, and each alone: one line, JSON as it was."""
    report = _verification_quoting(detail)
    monkeypatch.setattr(use_cases, "verify", lambda request: report)
    argv = _verify_argv(Case(tmp_path), "--environment", ENVIRONMENT_REF)

    code, out, err = _answer(argv)
    json_code, printed, json_err = _answer([*argv, "--json"])
    ascii_code, ascii_out, ascii_err = _run(argv, "ascii")

    said = [
        "review   results.provenance     provenance.other_environment",
        "         " + said,
        f"run      run-{'0' * 16}  evidence {DIGEST}",
        "status   completed",
        "verdict: needs_review (0 blocking check(s))",
    ]
    assert _lines(out) == said
    assert _verdicts(out) == said[-1:]
    document = report.model_dump(mode="json")
    assert printed == json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    assert ascii_out == "".join(
        line.encode("ascii", "backslashreplace").decode("ascii") + "\n" for line in said
    )
    assert (code, err, json_code, json_err, ascii_code, ascii_err) == (3, "", 3, "", 3, "")


def test_evidence_spells_a_caveat_a_run_wrote_of_an_attestation(tmp_path: Path) -> None:
    case = _forged_run(tmp_path)
    assert _answer(_verify_argv(case, "--environment", ENVIRONMENT_REF))[0] == 3
    evidence = case.root / "evidence"
    argv = ["decision", "evidence", str(evidence)]

    code, out, err = _answer(argv)
    json_code, printed, document = _json_answer(argv)

    (claim,) = document["bundle"]["claims"]
    assert claim["caveats"][1] == f"provenance.other_environment: {PROVENANCE}"
    spelled = PROVENANCE.replace(FORGED, FORGED_SAID)
    assert f"           caveat: provenance.other_environment: {spelled}" in _lines(out)
    assert _verdicts(out) == ["verdict: pass (0 problem(s))"]
    assert (code, err, json_code) == (0, "", 0)
    store = FilesystemArtifactStore(evidence)
    assert printed == _dumped(evidence_inspect.inspect_bundle(store, document["run_id"]))


# --- decision evidence over a bundle someone else wrote ------------------------------------


def _bundle(root: Path) -> Path:
    """A bundle that hashes to its addresses, its version and caveat as its writer chose."""
    builder = BundleWriter(
        store=FilesystemArtifactStore(root),
        run_id=RUN,
        product_ref="decision://sales/weekly_margin@1",
        verifier_version=VERSION,
        created_at="2026-08-24T09:15:00Z",
    )
    ref = builder.add_check("freshness", {"status": "pass"})
    builder.add_claim(
        claim_id="margin-drop",
        claim_type=ClaimType.OBSERVATION,
        summary_code="margin.candidate_differs",
        evidence_refs=[ref],
        caveats=[CAVEAT],
    )
    builder.write()
    return root


def test_evidence_spells_the_version_and_the_caveat_a_bundle_holds(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    argv = ["decision", "evidence", str(root)]

    code, out, err = _answer(argv)
    json_code, printed, document = _json_answer(argv)

    assert document["bundle"]["verifier_version"] == VERSION
    assert document["bundle"]["claims"][0]["caveats"] == [CAVEAT]
    assert _lines(out) == [
        f"run       {RUN}",
        f"digest    {document['manifest_digest']}",
        "product   decision://sales/weekly_margin@1",
        f"created   2026-08-24T09:15:00Z   verifier 0.5.0{FORGED_SAID}",
        "integrity content_hashed",
        "claim     observation   margin.candidate_differs ",
        "           margin-drop <- check-freshness",
        f"           caveat: checked by hand{FORGED_SAID}",
        "artifact  internal      check_result             17 B  check-freshness",
        "verdict: pass (0 problem(s))",
    ]
    assert (code, err, json_code) == (0, "", 0)
    assert printed == _dumped(evidence_inspect.inspect_bundle(FilesystemArtifactStore(root), RUN))


def test_evidence_spells_the_digest_its_caller_pinned(tmp_path: Path) -> None:
    root = _bundle(tmp_path)
    digest = evidence_inspect.inspect_bundle(FilesystemArtifactStore(root), RUN).manifest_digest

    code, out, err = _answer(
        ["decision", "evidence", str(root), "--expect-digest", DIGEST + FORGED]
    )

    assert _lines(out)[-2:] == [
        "block     evidence.digest_unexpected   "
        f"manifest hashes to {digest}, the caller expected {DIGEST}{FORGED_SAID}",
        "verdict: block (1 problem(s))",
    ]
    assert _verdicts(out) == ["verdict: block (1 problem(s))"]
    assert (code, err) == (4, "")


@pytest.mark.parametrize(("value", "said"), [pytest.param(RAW, SAID, id="RAW"), *ALONE])
def test_evidence_spells_every_line_it_prints_of_what_it_read(
    monkeypatch: pytest.MonkeyPatch, value: str, said: str
) -> None:
    """A bundle whose every value is *value*, as a reader of another store could answer."""
    claim = SimpleNamespace(
        claim_type=ClaimType.OBSERVATION,
        summary_code=value,
        claim_id=value,
        evidence_refs=[value, value],
        caveats=[value],
    )
    artifact = SimpleNamespace(
        classification=EvidenceClassification.INTERNAL,
        kind=ArtifactKind.REPORT,
        size_bytes=17,
        artifact_id=value,
    )
    bundle = SimpleNamespace(
        product_ref=value,
        created_at=value,
        verifier_version=value,
        integrity=EvidenceIntegrity.SIGNED,
        signature=SimpleNamespace(key_id=value),
        claims=[claim],
        artifacts=[artifact],
    )
    inspection = SimpleNamespace(
        run_id=value,
        manifest_digest=DIGEST,
        bundle=bundle,
        signature_verified=False,
        problems=[SimpleNamespace(severity=Severity.BLOCK, code=value, detail=value)],
        verdict=Verdict.BLOCK,
    )
    located = SimpleNamespace(store=None, run_id=value)
    monkeypatch.setattr(use_cases, "locate_evidence", lambda path, run: located)
    monkeypatch.setattr(evidence_inspect, "inspect_bundle", lambda *_, **__: inspection)

    code, out, err = _answer(["decision", "evidence", "bundle"])

    assert _lines(out) == [
        f"run       {said}",
        f"digest    {DIGEST}",
        f"product   {said}",
        f"created   {said}   verifier {said}",
        "integrity signed",
        f"signature unverified   key {said}",
        f"claim     observation   {_padded(value, said, 24)} ",
        f"           {said} <- {said}, {said}",
        f"           caveat: {said}",
        f"artifact  {'internal':<13} {'report':<18} {17:>8} B  {said}",
        f"block     {_padded(value, said, 28)} {said}",
        "verdict: block (1 problem(s))",
    ]
    assert (code, err) == (4, "")


# --- decision runs --------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "said"), [pytest.param(RAW, SAID, id="RAW"), *ALONE])
def test_runs_spells_what_a_repository_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, said: str
) -> None:
    """The store of files lists no such run; a row of another store is spelled all the same."""
    row = RunSummary(
        run_id=value,
        manifest_run_id=value,
        product_ref=value,
        created_at=value,
        manifest_digest=value,
    )
    monkeypatch.setattr(FilesystemRunRepository, "list_runs", lambda self: [row])

    code, out, err = _answer(["decision", "runs", str(tmp_path)])
    json_code, printed, json_err = _answer(["decision", "runs", str(tmp_path), "--json"])

    assert _lines(out) == [
        "Local runs (integrity_checked=false; use decision evidence to verify):",
        f"{said}  {value!r}  {value!r}  {said}  manifest_run_id={said}",
    ]
    assert (code, err) == (0, "")
    assert printed == json.dumps([row.model_dump(mode="json")], ensure_ascii=False, indent=2) + "\n"
    assert (json_code, json_err) == (0, "")


# --- what is not spelled ------------------------------------------------------------------


@pytest.mark.parametrize("command", ["capture", "investigate"])
def test_capture_and_investigate_name_what_they_wrote_as_they_were_given_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    stand = _Stand(tmp_path, monkeypatch)
    output = stand.out / f"out{LS}put"

    answer = stand.run(_flag(getattr(stand, command)(), "--output", output))

    assert answer.code == 0
    assert answer.out.split("\n")[0] == f"wrote    {output}"
