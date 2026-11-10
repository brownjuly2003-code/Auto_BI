"""What a command line states of a run is settled before the run starts.

`decision verify` is told the moment the bundle is of (`--created-at`) and the environment
the verdict is about (`--environment`); `decision capture` is told the moment of the capture
(`--captured-at`) and the environment, and attests the configured user with them. A moment
the manifest does not take ended `decision verify` in an unhandled exception after every
input was loaded and graded, and `decision capture` in four lines of pydantic's listing
after the query was run. An environment that holds a lone surrogate — what a byte of a
command line that is no UTF-8 is decoded to — ended `decision verify` in an unhandled
exception, and `decision capture` wrote a file no command reads back
(`docs/specs/stated-run-facts.md`).
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from auto_bi import cli
from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.use_cases import (
    CaptureReport,
    CaptureRequest,
    VerifyError,
    VerifyRequest,
    capture_result,
    verify,
)
from auto_bi.scalars import Timestamp
from auto_bi.verification.model import EXIT_ERROR
from auto_bi.verification.release_gate import GateReport
from auto_bi.verification.result_probe import QueryColumn, QueryResult, capture
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT, ENVIRONMENT_REF, Case
from tests.test_result_probe import CANDIDATE_DIGEST, _asset
from tests.test_unreadable_run_inputs import _assets, _case

NL = chr(10)
TAB = chr(9)
#: Half of the UTF-16 form of a character: no character, and no UTF-8 form.
LONE = chr(0xD800)
#: What a byte of a command line that is no UTF-8 is decoded to (`surrogateescape`).
UNDECODED = chr(0xDCFF)

NOT_A_MOMENT = "must be an RFC 3339 UTC timestamp such as 2026-08-25T09:00:00Z"
NOT_TEXT = "must be text without a lone surrogate"
CAPTURE_ENVIRONMENT = "--environment, or the configured host:port/database it defaults to,"
CAPTURE_ACTOR = "the configured DWH user"

#: Moments a manifest and an attestation take.
MOMENTS = [
    pytest.param(CREATED_AT, id="to the second"),
    pytest.param("2026-08-25T09:00:00.123Z", id="a fraction of a second"),
    pytest.param("2026-08-25T09:00:00.123456789Z", id="to the nanosecond"),
    pytest.param("0001-01-01T00:00:00Z", id="the first second there is"),
    pytest.param("9999-12-31T23:59:59Z", id="the last second there is"),
]
#: Text that is no such moment, as a flag can give it: a flag given as empty text is a flag
#: not given, and the command reads the clock.
GIVEN_NOT_MOMENTS = [
    pytest.param("yesterday", id="a word"),
    pytest.param("2026-08-25T09:00:00+03:00", id="an offset for Z"),
    pytest.param("2026-08-25T09:00:00", id="no zone"),
    pytest.param(" 2026-08-25T09:00:00Z", id="a blank ahead"),
    pytest.param("2026-08-25T09:00:00Z" + NL, id="a line break behind"),
    pytest.param("2026-08-25 09:00:00Z", id="a blank for T"),
    pytest.param("2026-02-30T09:00:00Z", id="no such day"),
    pytest.param("2026-08-25T25:00:00Z", id="no such hour"),
    pytest.param("2026-08-25T09:00:00z", id="a small z"),
    pytest.param("2026-08-25T09:00:00Z" + LONE, id="a lone surrogate behind"),
    pytest.param("2026-08-25T09:00:00" + UNDECODED + "Z", id="a byte that is no UTF-8"),
    pytest.param("0000-01-01T00:00:00Z", id="the year 0000"),
    pytest.param("2026-08-25", id="a date alone"),
    pytest.param("2026-08-25T09:00:00.123456" + NL + "verdict: passZ", id="a line in it"),
    pytest.param("2026-08-25T09:00Z", id="no seconds"),
    pytest.param("20260825T090000Z", id="no separators"),
    pytest.param("2026-W35-2T09:00:00Z", id="a week and a day"),
    pytest.param("2026-08-25T09:00:00,5Z", id="a comma for the point"),
    pytest.param("2026-08-25T24:00:00Z", id="the hour 24"),
    pytest.param("   ", id="blanks"),
]
#: Text that is no such moment, as a request can state it.
NOT_MOMENTS = [*GIVEN_NOT_MOMENTS, pytest.param("", id="nothing")]
#: Environments and users that are text: each is taken as it is given.
TEXTS = [
    pytest.param("   ", id="blanks"),
    pytest.param("stage" + NL + "prod", id="two lines"),
    pytest.param(ENVIRONMENT_REF + " ", id="a blank behind"),
    pytest.param("stage" + TAB + "prod", id="a tab inside"),
    pytest.param("stage" + chr(0) + "prod", id="a NUL inside"),
    pytest.param("стенд:9000/dm", id="letters outside ASCII"),
    pytest.param("stage-" + chr(0x1F600), id="a character outside the basic plane"),
]
#: Text evidence has no form for.
NOT_TEXTS = [
    pytest.param("stage" + LONE, id="a lone surrogate"),
    pytest.param("stage" + UNDECODED, id="a byte that is no UTF-8"),
    pytest.param(chr(0xD83D) + chr(0xDE00), id="two surrogates side by side"),
    pytest.param(LONE, id="nothing but a lone surrogate"),
]


class _Engine:
    """A capture target that counts what it is asked for."""

    def __init__(self, environment: str = ENVIRONMENT_REF, user: str = "auto_bi_ro") -> None:
        self.environment = environment
        self.user = user
        self.opened = 0
        self.asked = 0

    def factory(self) -> Any:
        self.opened += 1
        return self.query

    def query(self, _sql: str) -> QueryResult:
        self.asked += 1
        return QueryResult(
            columns=(
                QueryColumn("region", "String", nullable=False),
                QueryColumn("gross_margin_pct", "Decimal(18, 6)", nullable=False),
            ),
            rows=(("Москва", Decimal("0.412000")),),
        )

    def target(self, engine: str) -> tuple[Any, str, str]:
        assert engine == "clickhouse"
        return self.factory, self.environment, self.user


def _no_engine(_questions: Any) -> Any:
    raise AssertionError("no engine may be asked for a run that is refused")


def _verify(case: Case, **stated: Any) -> GateReport:
    fields: dict[str, Any] = {
        "contract_paths": [case.contracts],
        "base": case.base,
        "candidate": case.candidate,
        "evidence_dir": case.root / "evidence",
        "created_at": CREATED_AT,
        "dashboards_dir": case.dashboards,
        "assets_dir": case.assets,
        "results_file": case.results,
    }
    return verify(VerifyRequest(**{**fields, **stated}))


def _verify_refused(case: Case, **stated: Any) -> str:
    """What `decision verify` refuses *case* with: nothing asked and nothing written."""
    with pytest.raises(VerifyError) as caught:
        _verify(case, probe=_no_engine, **stated)
    assert not (case.root / "evidence").exists()
    return str(caught.value)


def _capture(case: Case, engine: _Engine, **stated: Any) -> CaptureReport:
    fields: dict[str, Any] = {
        "assets": _assets(case),
        "candidate": case.candidate,
        "output": case.root / "captured.yaml",
        "engine": "clickhouse",
        "environment_ref": ENVIRONMENT_REF,
        "executed_by": "auto_bi_ro",
        "captured_at": CREATED_AT,
        "query_factory": engine.factory,
        "asset_id": ASSET_ID,
    }
    return capture_result(CaptureRequest(**{**fields, **stated}))


def _capture_refused(case: Case, **stated: Any) -> str:
    """What `decision capture` refuses *case* with: no engine opened, nothing written."""
    engine = _Engine()
    with pytest.raises(VerifyError) as caught:
        _capture(case, engine, **stated)
    assert (engine.opened, engine.asked) == (0, 0)
    assert not (case.root / "captured.yaml").exists()
    return str(caught.value)


def _attested(case: Case) -> dict[str, Any]:
    """The attestation of the result a capture wrote for *case*."""
    text = (case.root / "captured.yaml").read_text(encoding="utf-8")
    attestation: dict[str, Any] = yaml.safe_load(text)[ASSET_ID]["attestation"]
    return attestation


def _capture_argv(case: Case, *extra: str) -> list[str]:
    argv = ["decision", "capture", str(_assets(case)), "--candidate", str(case.candidate)]
    argv += ["--asset", ASSET_ID, "--dwh", "clickhouse"]
    return [*argv, "--output", str(case.root / "captured.yaml"), *extra]


# --- the moment of a run ----------------------------------------------------------------


@pytest.mark.parametrize("moment", NOT_MOMENTS)
def test_a_run_of_a_moment_that_is_none_is_refused_by_its_flag(tmp_path: Path, moment: str) -> None:
    assert _verify_refused(_case(tmp_path), created_at=moment) == f"--created-at {NOT_A_MOMENT}"


@pytest.mark.parametrize("moment", MOMENTS)
def test_a_run_takes_the_moments_a_manifest_takes(tmp_path: Path, moment: str) -> None:
    case = _case(tmp_path)

    report = _verify(case, created_at=moment)

    manifest = case.root / "evidence" / report.run_id / "manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8"))["created_at"] == moment


@pytest.mark.parametrize("moment", [*MOMENTS, *NOT_MOMENTS])
def test_the_moments_refused_are_the_ones_a_manifest_refuses(tmp_path: Path, moment: str) -> None:
    case = _case(tmp_path)
    try:
        TypeAdapter(Timestamp).validate_python(moment)
    except ValidationError:
        taken_by_the_schema = False
    else:
        taken_by_the_schema = True

    try:
        _verify(case, created_at=moment)
    except VerifyError:
        taken_by_the_run = False
    else:
        taken_by_the_run = True

    assert taken_by_the_run is taken_by_the_schema


# --- the environment of a run -----------------------------------------------------------


@pytest.mark.parametrize("environment", NOT_TEXTS)
def test_a_run_about_an_environment_that_is_no_text_is_refused_by_its_flag(
    tmp_path: Path, environment: str
) -> None:
    refusal = _verify_refused(_case(tmp_path), environment_ref=environment)

    assert refusal == f"--environment {NOT_TEXT}"


@pytest.mark.parametrize("environment", TEXTS)
def test_a_run_takes_an_environment_that_is_text_as_it_is_given(
    tmp_path: Path, environment: str
) -> None:
    stated = _verify(_case(tmp_path / "stated"), environment_ref=environment)
    other = _verify(_case(tmp_path / "other"), environment_ref=ENVIRONMENT_REF)

    # Nothing of it is trimmed or folded: the run is another one than that of any other text.
    assert stated.run_id != other.run_id
    assert (tmp_path / "stated" / "evidence" / stated.run_id / "manifest.json").is_file()


def test_a_run_that_states_no_environment_is_taken(tmp_path: Path) -> None:
    case = _case(tmp_path)

    report = _verify(case, environment_ref="")

    assert (case.root / "evidence" / report.run_id / "manifest.json").is_file()


# --- before the run ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stated", "refusal"),
    [
        pytest.param({"created_at": "yesterday"}, f"--created-at {NOT_A_MOMENT}", id="the moment"),
        pytest.param(
            {"environment_ref": "stage" + LONE}, f"--environment {NOT_TEXT}", id="the environment"
        ),
    ],
)
def test_what_a_run_states_is_refused_before_anything_is_read(
    tmp_path: Path, stated: dict[str, str], refusal: str
) -> None:
    nowhere = tmp_path / "nowhere"
    request = VerifyRequest(
        **{
            "contract_paths": [nowhere],
            "base": nowhere,
            "candidate": nowhere,
            "evidence_dir": tmp_path / "evidence",
            "created_at": CREATED_AT,
            "dashboards_dir": nowhere,
            "assets_dir": nowhere,
            "results_file": nowhere,
            "probe": _no_engine,
            **stated,
        }
    )

    with pytest.raises(VerifyError) as caught:
        verify(request)

    # None of the paths is there, and none of them is what the refusal is about.
    assert str(caught.value) == refusal
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize(
    "stated",
    [
        pytest.param({"created_at": "yesterday"}, id="the moment"),
        pytest.param({"environment_ref": "stage" + LONE}, id="the environment"),
    ],
)
def test_no_contract_is_loaded_for_a_run_that_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stated: dict[str, str]
) -> None:
    """Paths that are not there are read without a word, so the loading itself is watched."""
    case = _case(tmp_path)
    loaded: list[Any] = []
    load = use_cases.load_contracts

    def watched(paths: Any) -> Any:
        loaded.append(list(paths))
        return load(paths)

    monkeypatch.setattr(use_cases, "load_contracts", watched)

    _verify_refused(case, **stated)
    assert loaded == []

    # The run that is taken loads them through the name that is watched, and once.
    _verify(case)
    assert loaded == [[case.contracts]]


def test_the_moment_of_a_run_is_refused_ahead_of_its_environment(tmp_path: Path) -> None:
    refusal = _verify_refused(
        _case(tmp_path), created_at="yesterday", environment_ref="stage" + LONE
    )

    assert refusal == f"--created-at {NOT_A_MOMENT}"


# --- the moment of a capture ------------------------------------------------------------


@pytest.mark.parametrize("moment", NOT_MOMENTS)
def test_a_capture_of_a_moment_that_is_none_is_refused_by_its_flag(
    tmp_path: Path, moment: str
) -> None:
    refusal = _capture_refused(_case(tmp_path), captured_at=moment)

    assert refusal == f"--captured-at {NOT_A_MOMENT}"


@pytest.mark.parametrize("moment", MOMENTS)
def test_a_capture_takes_the_moments_an_attestation_takes(tmp_path: Path, moment: str) -> None:
    case = _case(tmp_path)
    engine = _Engine()

    report = _capture(case, engine, captured_at=moment)

    assert (engine.opened, engine.asked, report.row_count) == (1, 1, 1)
    assert _attested(case)["captured_at"] == moment


# --- the environment and the actor of a capture -----------------------------------------


@pytest.mark.parametrize("environment", NOT_TEXTS)
def test_a_capture_for_an_environment_that_is_no_text_is_refused(
    tmp_path: Path, environment: str
) -> None:
    refusal = _capture_refused(_case(tmp_path), environment_ref=environment)

    assert refusal == f"{CAPTURE_ENVIRONMENT} {NOT_TEXT}"


@pytest.mark.parametrize("user", NOT_TEXTS)
def test_a_capture_by_a_user_that_is_no_text_is_refused(tmp_path: Path, user: str) -> None:
    refusal = _capture_refused(_case(tmp_path), executed_by=user)

    assert refusal == f"{CAPTURE_ACTOR} {NOT_TEXT}"


@pytest.mark.parametrize("text", TEXTS)
def test_a_capture_attests_an_environment_and_a_user_that_are_text_as_given(
    tmp_path: Path, text: str
) -> None:
    case = _case(tmp_path)
    engine = _Engine()

    _capture(case, engine, environment_ref=text, executed_by=text)

    attested = _attested(case)
    assert (engine.opened, engine.asked) == (1, 1)
    assert (attested["environment_ref"], attested["executed_by"]) == (text, text)


def test_a_capture_that_states_no_environment_and_no_user_is_taken(tmp_path: Path) -> None:
    case = _case(tmp_path)

    _capture(case, _Engine(), environment_ref="", executed_by="")

    attested = _attested(case)
    assert (attested["environment_ref"], attested["executed_by"]) == ("", "")


# --- before the capture -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("stated", "refusal"),
    [
        pytest.param(
            {"captured_at": "yesterday"}, f"--captured-at {NOT_A_MOMENT}", id="the moment"
        ),
        pytest.param(
            {"environment_ref": "stage" + LONE},
            f"{CAPTURE_ENVIRONMENT} {NOT_TEXT}",
            id="the environment",
        ),
        pytest.param(
            {"executed_by": "reader" + LONE}, f"{CAPTURE_ACTOR} {NOT_TEXT}", id="the user"
        ),
    ],
)
def test_what_a_capture_states_is_refused_before_anything_is_looked_at(
    tmp_path: Path, stated: dict[str, str], refusal: str
) -> None:
    nowhere = tmp_path / "nowhere"
    taken = tmp_path / "taken.yaml"
    taken.write_text("kept: true\n", encoding="utf-8")
    engine = _Engine()
    request = CaptureRequest(
        **{
            "assets": nowhere,
            "candidate": nowhere,
            "output": taken,
            "engine": "an engine there is none of",
            "environment_ref": ENVIRONMENT_REF,
            "executed_by": "auto_bi_ro",
            "captured_at": CREATED_AT,
            "query_factory": engine.factory,
            **stated,
        }
    )

    with pytest.raises(VerifyError) as caught:
        capture_result(request)

    # The output is taken, the assets and the ref are not there, the engine is none: the
    # refusal is about none of them.
    assert str(caught.value) == refusal
    assert (engine.opened, engine.asked) == (0, 0)
    assert taken.read_text(encoding="utf-8") == "kept: true\n"


@pytest.mark.parametrize(
    ("stated", "refusal"),
    [
        pytest.param(
            {"captured_at": "yesterday", "environment_ref": LONE, "executed_by": LONE},
            f"--captured-at {NOT_A_MOMENT}",
            id="the moment ahead of the environment and the user",
        ),
        pytest.param(
            {"environment_ref": LONE, "executed_by": LONE},
            f"{CAPTURE_ENVIRONMENT} {NOT_TEXT}",
            id="the environment ahead of the user",
        ),
    ],
)
def test_what_a_capture_states_is_refused_in_one_order(
    tmp_path: Path, stated: dict[str, str], refusal: str
) -> None:
    assert _capture_refused(_case(tmp_path), **stated) == refusal


# --- the attestation and the query ------------------------------------------------------


def test_a_result_is_attested_before_its_query_is_run() -> None:
    asked: list[str] = []

    def run_query(sql: str) -> QueryResult:
        asked.append(sql)
        return QueryResult(columns=(), rows=())

    with pytest.raises(ValidationError) as caught:
        capture(
            _asset(),
            run_query,
            candidate_digest=CANDIDATE_DIGEST,
            environment_ref=ENVIRONMENT_REF,
            executed_by="auto_bi_ro",
            captured_at="yesterday",
        )

    assert caught.value.title == "ResultAttestation"
    assert asked == []


# --- the commands -----------------------------------------------------------------------


@pytest.mark.parametrize("moment", GIVEN_NOT_MOMENTS)
def test_decision_verify_refuses_a_moment_with_one_line_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], moment: str
) -> None:
    case = _case(tmp_path)
    argv = _verify_argv(case)
    argv[argv.index("--created-at") + 1] = moment

    assert main(argv) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: --created-at {NOT_A_MOMENT}\n"
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("environment", NOT_TEXTS)
def test_decision_verify_refuses_an_environment_with_one_line_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], environment: str
) -> None:
    case = _case(tmp_path)

    assert main(_verify_argv(case, "--environment", environment)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: --environment {NOT_TEXT}\n"
    assert not (case.root / "evidence").exists()


@pytest.mark.parametrize("moment", GIVEN_NOT_MOMENTS)
def test_decision_capture_refuses_a_moment_with_one_line_and_asks_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    moment: str,
) -> None:
    case = _case(tmp_path)
    engine = _Engine()
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)

    assert main(_capture_argv(case, "--captured-at", moment)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: --captured-at {NOT_A_MOMENT}\n"
    assert (engine.opened, engine.asked) == (0, 0)
    assert not (case.root / "captured.yaml").exists()


@pytest.mark.parametrize("environment", NOT_TEXTS)
def test_decision_capture_refuses_an_environment_with_one_line_and_asks_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    case = _case(tmp_path)
    engine = _Engine()
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)
    argv = _capture_argv(case, "--captured-at", CREATED_AT, "--environment", environment)

    assert main(argv) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {CAPTURE_ENVIRONMENT} {NOT_TEXT}\n"
    assert (engine.opened, engine.asked) == (0, 0)
    assert not (case.root / "captured.yaml").exists()


@pytest.mark.parametrize(
    ("configured", "refusal"),
    [
        pytest.param(
            {"environment": "stage" + UNDECODED},
            f"{CAPTURE_ENVIRONMENT} {NOT_TEXT}",
            id="the configured environment",
        ),
        pytest.param({"user": "reader" + UNDECODED}, f"{CAPTURE_ACTOR} {NOT_TEXT}", id="the user"),
    ],
)
def test_decision_capture_refuses_what_is_configured_as_what_is_stated(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    configured: dict[str, str],
    refusal: str,
) -> None:
    case = _case(tmp_path)
    engine = _Engine(**configured)
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)

    assert main(_capture_argv(case, "--captured-at", CREATED_AT)) == EXIT_ERROR

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {refusal}\n"
    assert (engine.opened, engine.asked) == (0, 0)
    assert not (case.root / "captured.yaml").exists()


def test_decision_capture_attests_the_environment_it_is_told_not_the_configured_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    engine = _Engine(environment="stage" + UNDECODED)
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)
    argv = _capture_argv(case, "--captured-at", CREATED_AT, "--environment", ENVIRONMENT_REF)

    assert main(argv) == 0

    assert capsys.readouterr().err == ""
    assert _attested(case)["environment_ref"] == ENVIRONMENT_REF


# --- a flag given as empty text ---------------------------------------------------------


def test_a_moment_given_as_empty_text_is_a_moment_not_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    to_the_second = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    case = Case(tmp_path).with_candidate().with_asset()
    engine = _Engine()
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)
    argv = _verify_argv(case)
    argv[argv.index("--created-at") + 1] = ""

    # The clock is read in place of either: the commands run as they do without the flag.
    assert main(_capture_argv(case, "--captured-at", "")) == 0
    assert main(argv) != EXIT_ERROR

    assert capsys.readouterr().err == ""
    assert to_the_second.fullmatch(_attested(case)["captured_at"])
    (run,) = (case.root / "evidence").iterdir()
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert to_the_second.fullmatch(manifest["created_at"])


def test_an_environment_given_as_empty_text_is_the_configured_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(tmp_path)
    engine = _Engine(environment="clickhouse-prod:9000/dm")
    monkeypatch.setattr(cli, "_result_capture_target", engine.target)

    assert main(_capture_argv(case, "--captured-at", CREATED_AT, "--environment", "")) == 0

    assert capsys.readouterr().err == ""
    assert _attested(case)["environment_ref"] == "clickhouse-prod:9000/dm"
