"""A moment evidence carries has one RFC 3339 spelling, from the scalar to every reader.

Durable spec: docs/specs/utc-timestamps.md. `Timestamp` took text that ended in `Z`, held a
`T`, and that `datetime.fromisoformat` read with `+00:00` in place of the `Z`. With an offset
behind it, CPython's parser stops reading a fraction at its sixth digit, so any text between
that digit and the `Z` — a word, a line break, a control — was a moment and was sealed as one;
ISO 8601 forms RFC 3339 does not have (no seconds, no separators, a week, a comma) were moments
too, and `24:00:00` was one on Python 3.14 and not on 3.13. The form is now the scalar's own.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from auto_bi import scalars
from auto_bi.cli import main
from auto_bi.decision.validate import validate_paths
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.investigation.data import CanonicalMetricResult
from auto_bi.scalars import Timestamp
from auto_bi.semantic.gateway import NATIVE_SOURCE_VERSION
from auto_bi.semantic.metric_contract import MetricContract
from auto_bi.verification.model import EXIT_ERROR, Severity, Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, CheckStatus
from auto_bi.yaml_document import load_yaml_document
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    ASSET_ID,
    CREATED_AT,
    Case,
    _check_payload,
    _write_yaml,
    attestation,
    metric_contract,
    status_of,
)
from tests.test_investigation_decimal_exactness import raw_result
from tests.test_run_input_schema_refusals import _rewrite
from tests.test_unreadable_run_inputs import (
    ROWS,
    RUN_FILES,
    _capture_refused,
    _case,
    _refused,
)

NL = chr(10)
NOT_A_TIMESTAMP = "must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z"
NOT_TEXT = (
    "must be an RFC 3339 UTC timestamp as text, such as '2026-08-25T09:00:00Z' in quotes: "
    "YAML reads an unquoted one as a date"
)
#: What a text the scalar refuses ends a document in: pydantic's words, then the scalar's.
REFUSED_FIELD = f"Value error, {NOT_A_TIMESTAMP}"
#: Text after the sixth digit of a fraction: a line that reads like a key of the document.
INJECTED = "2026-08-25T08:00:00.123456" + NL + "verdict: passZ"

ADAPTER: TypeAdapter[str] = TypeAdapter(Timestamp)

#: RFC 3339 `date-time` in UTC: each is taken, and kept as it is written.
ACCEPTED = [
    pytest.param("2026-08-25T09:00:00Z", id="to the second"),
    pytest.param("2026-08-25T09:00:00.5Z", id="a tenth"),
    pytest.param("2026-08-25T09:00:00.500Z", id="a fraction with zeros behind"),
    pytest.param("2026-08-25T09:00:00.0Z", id="a fraction of nought"),
    pytest.param("2026-08-25T09:00:00.123456Z", id="to the microsecond"),
    pytest.param("2026-08-25T09:00:00.123456789Z", id="to the nanosecond"),
    pytest.param("2026-08-25T09:00:00." + "7" * 400 + "Z", id="a fraction of 400 digits"),
    pytest.param("2024-02-29T23:59:59Z", id="a leap day"),
    pytest.param("2000-02-29T00:00:00Z", id="a leap day of a century divisible by 400"),
    pytest.param("0001-01-01T00:00:00Z", id="the first second there is"),
    pytest.param("9999-12-31T23:59:59.999999999Z", id="the last moment there is"),
]

#: Text that is no such moment. The first two blocks, and the hour 24 on Python 3.14, were taken.
REFUSED = [
    # What was taken before: text between the sixth digit of a fraction and the `Z`.
    pytest.param(INJECTED, id="a line after the sixth digit"),
    pytest.param("2026-08-25T09:00:00.123456abcZ", id="letters after the sixth digit"),
    pytest.param("2026-08-25T09:00:00.1234567" + chr(7) + "Z", id="a control after the seventh"),
    pytest.param(
        "2026-08-25T09:00:00.123456" + chr(0x41C) + chr(0x43E) + "Z", id="a word after the sixth"
    ),
    pytest.param("2026-08-25T09:00:00.123456 Z", id="a blank after the sixth digit"),
    pytest.param(
        "2026-08-25T09:00:00.123456" + chr(0x661) + "Z", id="an Arabic-Indic digit after the sixth"
    ),
    # What ISO 8601 has and RFC 3339 has not, and was taken before.
    pytest.param("2026-08-25T09:00Z", id="no seconds"),
    pytest.param("2026-08-25T09Z", id="the hour alone"),
    pytest.param("2026-08-25T090000Z", id="the basic time"),
    pytest.param("20260825T090000Z", id="the basic date and time"),
    pytest.param("2026-W35-2T09:00:00Z", id="a week and a day"),
    pytest.param("2026-08-25T09:00:00,5Z", id="a comma for the point"),
    pytest.param("2026-08-25T09:00:00.Z", id="a point and no digit"),
    pytest.param("2026-08-25 09:00:00TZ", id="a blank for the T and a T before the Z"),
    # Taken by `datetime.fromisoformat` of Python 3.14 and not of 3.13.
    pytest.param("2026-08-25T24:00:00Z", id="the hour 24"),
    # Refused before, and still.
    pytest.param("2026-08-25T23:59:60Z", id="a leap second"),
    pytest.param("2026-08-25T09:60:00Z", id="the minute 60"),
    pytest.param("2025-02-29T09:00:00Z", id="no leap day"),
    pytest.param("1900-02-29T09:00:00Z", id="no leap day of a century"),
    pytest.param("2026-13-01T09:00:00Z", id="the month 13"),
    pytest.param("0000-01-01T00:00:00Z", id="the year 0000"),
    pytest.param("+2026-08-25T09:00:00Z", id="a sign ahead of the year"),
    pytest.param("12026-08-25T09:00:00Z", id="a year of five digits"),
    pytest.param("02026-08-25T09:00:00Z", id="a year of five digits with a nought ahead"),
    pytest.param("2026-008-25T09:00:00Z", id="a month of three digits"),
    pytest.param(
        chr(0x662) + chr(0x660) + chr(0x662) + chr(0x666) + "-08-25T09:00:00Z",
        id="Arabic-Indic digits",
    ),
    pytest.param("2026-08-25T09:00:0" + chr(0xFF10) + "Z", id="a fullwidth digit"),
    pytest.param(
        "2026-08-25T09:00:00." + chr(0x661) + "Z", id="an Arabic-Indic digit for the fraction"
    ),
    pytest.param("2026-08-25T09:00:00+00:00", id="an offset of nought for the Z"),
    pytest.param("2026-08-25T09:00:00+00:00Z", id="an offset before the Z"),
    pytest.param("2026-08-25T09:00:00z", id="a small z"),
    pytest.param("2026-08-25t09:00:00Z", id="a small t"),
    pytest.param("2026-08-25T09:00:00ZZ", id="two Z"),
    pytest.param("2026-08-25T09:00:00Z" + NL, id="a line break behind"),
    pytest.param(" 2026-08-25T09:00:00Z", id="a blank ahead"),
    pytest.param("2026-08-25", id="a date alone"),
    pytest.param("", id="nothing"),
]


def _refusal(value: object) -> str:
    with pytest.raises(ValidationError) as caught:
        ADAPTER.validate_python(value)
    (error,) = caught.value.errors()
    message: str = error["msg"]
    return message


# --- the scalar ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ACCEPTED)
def test_a_moment_of_the_one_spelling_is_taken_as_it_is_written(text: str) -> None:
    assert ADAPTER.validate_python(text) == text


@pytest.mark.parametrize("text", REFUSED)
def test_any_other_text_is_refused_and_told_the_spelling(text: str) -> None:
    assert _refusal(text) == REFUSED_FIELD


@pytest.mark.parametrize(
    "value", [None, 20260825, 1.5, b"2026-08-25T09:00:00Z", ["2026-08-25T09:00:00Z"]]
)
def test_a_value_that_is_not_text_is_refused(value: object) -> None:
    assert _refusal(value) == REFUSED_FIELD


@pytest.mark.parametrize(
    "line",
    [
        "at: 2026-08-25T09:00:00Z",
        "at: 2026-08-25T09:00:00.5Z",
        "at: 2026-08-25 09:00:00",
        "at: 2026-08-25",
    ],
)
def test_a_date_yaml_reads_of_an_unquoted_moment_is_told_to_quote_it(line: str) -> None:
    value = load_yaml_document(line)["at"]
    assert isinstance(value, date)

    assert _refusal(value) == f"Value error, {NOT_TEXT}"


def test_a_date_and_time_a_program_hands_it_is_refused_alike() -> None:
    assert _refusal(datetime(2026, 8, 25, 9, tzinfo=UTC)) == f"Value error, {NOT_TEXT}"
    assert _refusal(date(2026, 8, 25)) == f"Value error, {NOT_TEXT}"


def test_the_quoted_moment_of_the_same_document_is_taken() -> None:
    value = load_yaml_document("at: '2026-08-25T09:00:00Z'")["at"]

    assert ADAPTER.validate_python(value) == "2026-08-25T09:00:00Z"


class _ReadsAnything(datetime):
    """A `datetime` whose `fromisoformat` takes every text, as a later interpreter might."""

    @classmethod
    def fromisoformat(cls, date_string: str) -> datetime:
        return datetime(2026, 8, 25, tzinfo=UTC)


class _ReadsNothing(datetime):
    """A `datetime` whose `fromisoformat` takes no text."""

    @classmethod
    def fromisoformat(cls, date_string: str) -> datetime:
        raise ValueError(f"Invalid isoformat string: {date_string!r}")


@pytest.mark.parametrize("reader", [_ReadsAnything, _ReadsNothing], ids=["anything", "nothing"])
def test_the_answer_is_not_what_fromisoformat_reads(
    monkeypatch: pytest.MonkeyPatch, reader: type[datetime]
) -> None:
    """Python 3.14 reads `24:00` and 3.13 does not; the scalar asks neither of them."""
    monkeypatch.setattr(scalars, "datetime", reader)
    adapter: TypeAdapter[str] = TypeAdapter(scalars.Timestamp)

    for text in ("2026-08-25T24:00:00Z", "2025-02-29T09:00:00Z", "2026-08-25T09:00Z"):
        with pytest.raises(ValidationError):
            adapter.validate_python(text)
    for text in ("2026-08-25T09:00:00Z", "2026-08-25T09:00:00.123456789Z"):
        assert adapter.validate_python(text) == text


# --- what reads a moment ------------------------------------------------------------------


def test_a_recorded_result_attested_at_no_such_moment_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The text after the sixth digit was sealed in the attestation, its age counted."""
    case = Case(tmp_path).with_asset()
    case.with_results(ROWS, attestation(case.candidate_digest, captured_at=INJECTED))
    expected = (
        f"{case.results} holds an invalid recorded result for {ASSET_ID!r}: "
        f"attestation.captured_at: {REFUSED_FIELD}"
    )

    assert _refused(case) == expected

    assert main(_verify_argv(case)) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {expected}\n"
    assert not (case.root / "evidence").exists()


def test_a_recorded_result_attested_to_the_nanosecond_is_sealed_as_it_is_written(
    tmp_path: Path,
) -> None:
    """The fraction is kept as written; the age is counted to the microsecond."""
    captured_at = "2026-08-25T08:59:59.999999999Z"
    case = Case(tmp_path).with_asset()
    case.with_results(ROWS, attestation(case.candidate_digest, captured_at=captured_at))

    report = case.run()

    assert status_of(report, "results.provenance") is CheckStatus.PASS
    sealed = json.dumps(_check_payload(tmp_path, report, "results.provenance"))
    assert json.dumps(captured_at) in sealed


def test_an_asset_reviewed_at_no_such_moment_is_refused_by_both_commands(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a verified asset"](case)

    def change(document: Any) -> None:
        document["review"]["reviewed_at"] = "2026-08-20T10:00Z"

    _rewrite(path, change)
    expected = f"{path} is not a valid verified asset: review.reviewed_at: {REFUSED_FIELD}"

    assert _refused(case) == expected
    assert _capture_refused(case) == expected


def test_an_asset_reviewed_at_an_unquoted_moment_is_told_to_quote_it(tmp_path: Path) -> None:
    case = _case(tmp_path)
    path = RUN_FILES["a verified asset"](case)
    text = path.read_text(encoding="utf-8")
    quoted = "reviewed_at: '2026-08-20T10:00:00Z'"
    assert quoted in text
    path.write_text(text.replace(quoted, "reviewed_at: 2026-08-20T10:00:00Z"), encoding="utf-8")

    assert _refused(case) == (
        f"{path} is not a valid verified asset: review.reviewed_at: Value error, {NOT_TEXT}"
    )


def _reviewed_sql(reviewed_at: str) -> dict[str, Any]:
    contract = metric_contract()
    contract["formula"] = {
        "kind": "reviewed_sql",
        "sql": "sum(gross_margin) / sum(revenue)",
        "review": {
            "reviewer": "commercial-analytics",
            "reviewed_at": reviewed_at,
            "rationale": "no governed source for the legacy definition yet",
        },
    }
    return contract


def test_a_metric_formula_reviewed_at_no_such_moment_fails_its_schema() -> None:
    MetricContract.model_validate(_reviewed_sql("2026-08-21T09:00:00Z"))

    with pytest.raises(ValidationError) as caught:
        MetricContract.model_validate(_reviewed_sql("2026-08-21T09:00Z"))

    (error,) = caught.value.errors()
    assert error["loc"] == ("formula", "review", "reviewed_at")
    assert error["msg"] == REFUSED_FIELD


def test_decision_validate_and_the_run_block_on_such_a_metric_contract(tmp_path: Path) -> None:
    case = Case(tmp_path).with_asset().with_results(ROWS)
    _write_yaml(case.contracts / "gross_margin_pct.yaml", _reviewed_sql("2026-08-21T09:00Z"))

    report = validate_paths([case.contracts])

    (issue,) = [issue for issue in report.issues if issue.code == "contract.invalid"]
    assert issue.severity is Severity.BLOCK
    assert issue.source == "$1/gross_margin_pct.yaml"
    assert "formula.review.reviewed_at" in issue.message
    assert NOT_A_TIMESTAMP in issue.message
    assert report.verdict is Verdict.BLOCK

    run = case.run()
    assert status_of(run, "contracts.validate") is CheckStatus.BLOCK
    assert run.verdict is Verdict.BLOCK


def test_a_ref_that_holds_such_a_metric_contract_carries_a_blocking_gap(tmp_path: Path) -> None:
    contract = _reviewed_sql("2026-08-21T09:00Z")
    case = Case(tmp_path).with_candidate(contract=contract).with_asset().with_results(ROWS)

    report = case.run()

    assert status_of(report, "semantic.gaps") is CheckStatus.BLOCK
    gaps = _check_payload(tmp_path, report, "semantic.gaps")
    assert gaps["base"] == []
    assert [(gap["code"], gap["path"], gap["severity"]) for gap in gaps["candidate"]] == [
        ("gap.contract_invalid", "contracts.gross_margin_pct.yaml", "block")
    ]


@pytest.mark.parametrize("field", ["observed_at", "source_max_event_time"])
def test_the_data_of_an_investigation_observed_at_no_such_moment_is_refused(field: str) -> None:
    raw = raw_result()
    raw.update(
        observed_at="2026-09-04T12:00:00Z",
        source_max_event_time="2026-09-04T10:00:00Z",
        freshness_max_age="PT2H",
    )
    CanonicalMetricResult.model_validate(raw)
    raw[field] = "2026-09-04T12:00:00.123456" + NL + "complete: trueZ"

    with pytest.raises(ValidationError) as caught:
        CanonicalMetricResult.model_validate(raw)

    (error,) = caught.value.errors()
    assert error["loc"] == (field,)
    assert error["msg"] == REFUSED_FIELD


# --- a bundle sealed before ---------------------------------------------------------------


def _sealed_at(tmp_path: Path, moment: str) -> Path:
    """A bundle whose manifest says *moment*, as one sealed before T-118 could."""
    case = Case(tmp_path).with_asset().with_results(ROWS)
    report = case.run()
    manifest = case.root / "evidence" / report.run_id / "manifest.json"
    raw = manifest.read_bytes()
    stated = json.dumps(CREATED_AT).encode("utf-8")
    assert raw.count(stated) == 1
    manifest.write_bytes(raw.replace(stated, json.dumps(moment).encode("utf-8")))
    return manifest.parent


def test_a_bundle_sealed_at_another_spelling_is_not_read_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _sealed_at(tmp_path, "2026-08-25T09:00Z")

    assert main(["decision", "evidence", str(run), "--json"]) == 4
    inspection = json.loads(capsys.readouterr().out)
    assert [problem["code"] for problem in inspection["problems"]] == ["evidence.manifest_invalid"]

    assert main(["decision", "runs", str(run.parent)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (f"cannot discover runs at {run / 'manifest.json'}: ValidationError\n")


def test_a_bundle_sealed_at_the_one_spelling_is_read_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _sealed_at(tmp_path, CREATED_AT)

    assert main(["decision", "evidence", str(run), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []


def test_a_moment_of_another_spelling_in_an_artifact_is_not_read_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the manifest's moments are read: an artifact is checked by its digest and size."""
    stated = "2026-08-25T08:00:00.000Z"
    before = "2026-08-25T08:00:00,000Z"  # what a result attested before T-118 could be sealed with
    case = Case(tmp_path).with_asset()
    case.with_results(ROWS, attestation(case.candidate_digest, captured_at=stated))
    report = case.run()
    run = case.root / "evidence" / report.run_id
    manifest = run / "manifest.json"
    raw = manifest.read_bytes()
    rewritten = 0
    for path in sorted(run.rglob("*")):
        payload = path.read_bytes() if path.is_file() and path != manifest else b""
        if stated.encode("utf-8") not in payload:
            continue
        sealed = payload.replace(stated.encode("utf-8"), before.encode("utf-8"))
        old, new = (digest_bytes(data).encode("utf-8") for data in (payload, sealed))
        assert raw.count(old) == 1
        raw = raw.replace(old, new)
        path.write_bytes(sealed)
        rewritten += 1
    manifest.write_bytes(raw)

    assert rewritten
    assert json.dumps(before) in json.dumps(_check_payload(tmp_path, report, "results.provenance"))
    assert main(["decision", "evidence", str(run), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []


# --- versions ------------------------------------------------------------------------------


def test_the_gate_that_refuses_them_is_a_new_verifier_with_a_new_check_rule() -> None:
    """A contract that passed now blocks: the verdict of the same input moved (§7.6)."""
    assert int(VERIFIER_VERSION) >= 115
    assert int(CHECK_VERSION) >= 78
    assert NATIVE_SOURCE_VERSION == "3"
