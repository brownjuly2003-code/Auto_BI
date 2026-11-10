"""What a decision command says when it stops is one line, whatever it quotes.

A refusal quotes what it refuses: a path found under a directory, a key of a document, what a
validator or an engine said. Each may hold a line break, which split the error into lines a
pipeline reads as two messages, or a control a terminal acts on — an escape that clears the
screen. The line after `error: ` spells each character that is not printable by its escape,
as `decision investigate` already did; the paths that could not be read are spelled as a
diagnostic line spells a path, as the products their schema refused are.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.decision import use_cases
from auto_bi.decision.use_cases import VerifyRequest
from auto_bi.decision.validate import LoadedContracts, ValidationIssue, ValidationReport
from auto_bi.verification.model import EXIT_ERROR, Severity, Verdict
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT, PRODUCT_ID, Case
from tests.test_unreadable_run_inputs import _refused

NO_PRODUCT = "no decision product contract found in the given paths"
#: A message with a line break, a screen-clearing escape, a line separator, a zero-width
#: space, a right-to-left override, a lone surrogate, a backslash, a Cyrillic word with a
#: combining accent, a tab and a no-break space.
RAW = "a\nb\x1b[2Jc\u2028d\u200be\u202e\udc80f \\ g ме\u0301ра\th\u00a0i"
#: The same, as the line after `error: ` spells it.
SAID = "a\\nb\\x1b[2Jc\\u2028d\\u200be\\u202e\\udc80f \\ g ме\u0301ра\\th\\xa0i"
#: A key with an escape character, as YAML writes it in a double-quoted scalar.
ESCAPED_KEY = '"bad\\ekey": 1\n'


def _one_line_error(err: str) -> str:
    """The text after `error: ` of standard error, which holds that line and nothing else."""
    assert err.startswith("error: ")
    assert err.endswith("\n")
    assert err.count("\n") == 1
    assert all(char.isprintable() for char in err[:-1])
    return err[len("error: ") : -1]


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def _issue(source: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code="contract.unreadable",
        severity=Severity.BLOCK,
        subject=source,
        source=source,
        message=message,
    )


def _refused_over(case: Case, *paths: Path, product_id: str = "") -> str:
    """What `decision verify` refuses a run over *paths* with; nothing is written for it."""
    request = VerifyRequest(
        contract_paths=list(paths),
        base=case.base,
        candidate=case.base,
        evidence_dir=case.root / "evidence",
        created_at=CREATED_AT,
        dashboards_dir=case.dashboards,
        product_id=product_id,
    )
    with pytest.raises(use_cases.VerifyError) as caught:
        use_cases.verify(request)
    assert not (case.root / "evidence").exists()
    return str(caught.value)


# --- the paths that could not be read ------------------------------------------------------


def test_a_path_that_could_not_be_read_is_spelled_on_one_line(tmp_path: Path) -> None:
    """A validated path with a line break is named by its place: none of its text is spelled."""
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    missing = tmp_path / "missing\nline.yaml"

    assert _refused_over(case, case.contracts, missing) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: $2 (path does not exist)"
    )
    assert _refused_over(case, case.contracts, missing, product_id=PRODUCT_ID) == (
        f"no decision product '{PRODUCT_ID}' in the given paths (none); "
        "1 path(s) could not be read: $2 (path does not exist)"
    )


def test_the_command_says_it_in_one_line_with_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path)
    (case.contracts / "weekly_margin.yaml").unlink()
    missing = tmp_path / "missing\nline.yaml"
    argv = _verify_argv(case)
    argv.insert(argv.index(str(case.contracts)) + 1, str(missing))

    assert main(argv) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == (
        f"{NO_PRODUCT}; 1 path(s) could not be read: $2 (path does not exist)"
    )
    assert not (tmp_path / "evidence").exists()


def test_each_named_path_is_spelled_and_a_printable_one_as_it_is() -> None:
    """No file of a Windows path holds a control character: asked of what was loaded."""
    issues = [
        _issue("$1/plain.yaml", "cannot read contract: ParserError"),
        _issue("$1/line\nbreak.yaml", "cannot read contract: ParserError"),
        _issue("$1/tab\tdir", "cannot list directory: PermissionError"),
        _issue("$1/\x1b[2J.yaml", "cannot read contract: ParserError"),
    ]
    loaded = LoadedContracts(
        products={},
        metrics={},
        report=ValidationReport(verdict=Verdict.BLOCK, issues=issues),
    )

    with pytest.raises(use_cases.VerifyError) as caught:
        use_cases._select_product(loaded, "")

    assert str(caught.value) == (
        f"{NO_PRODUCT}; 4 path(s) could not be read: "
        "$1/plain.yaml (cannot read contract: ParserError), "
        "'$1/line\\nbreak.yaml' (cannot read contract: ParserError), "
        "'$1/tab\\tdir' (cannot list directory: PermissionError) (+1 more)"
    )


# --- what a document says, in the line of a command ----------------------------------------


def test_a_key_of_a_refused_product_is_escaped_on_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The message keeps the character; the line spells it by its escape."""
    case = Case(tmp_path)
    path = case.contracts / "weekly_margin.yaml"
    _append(path, ESCAPED_KEY)
    reason = "bad\x1bkey: Extra inputs are not permitted"
    assert _refused(case) == (
        f"{NO_PRODUCT}; 1 decision product(s) failed the schema: '{PRODUCT_ID}' in "
        f"$1/{path.name} "
        f"({reason})"
    )

    assert main(_verify_argv(case)) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == (
        f"{NO_PRODUCT}; 1 decision product(s) failed the schema: '{PRODUCT_ID}' in "
        f"$1/{path.name} "
        "(bad\\x1bkey: Extra inputs are not permitted)"
    )


def test_a_key_of_a_semantic_model_is_escaped_on_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = Case(tmp_path).with_candidate()
    model = case.candidate / "model.yaml"
    _append(model, ESCAPED_KEY)
    assert _refused(case) == (
        f"{model} is not a valid semantic model: bad\x1bkey: Extra inputs are not permitted"
    )

    assert main(_verify_argv(case)) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == (
        f"{model} is not a valid semantic model: bad\\x1bkey: Extra inputs are not permitted"
    )
    assert not (tmp_path / "evidence").exists()


def test_a_key_of_a_verified_asset_is_escaped_on_the_capture_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(tmp_path).with_candidate().with_asset()
    assert case.assets is not None
    asset = case.assets / "weekly_margin_by_region.yaml"
    _append(asset, ESCAPED_KEY)
    output = tmp_path / "captured.yaml"

    def target(engine: str) -> tuple[Any, str, str]:
        def factory() -> Any:
            raise AssertionError("no query may be opened for an asset the schema refuses")

        return factory, "clickhouse://test", "auto_bi_ro"

    monkeypatch.setattr("auto_bi.cli._result_capture_target", target)
    argv = [
        "decision",
        "capture",
        str(case.assets),
        "--candidate",
        str(case.candidate),
        "--asset",
        ASSET_ID,
        "--dwh",
        "clickhouse",
        "--output",
        str(output),
        "--captured-at",
        CREATED_AT,
    ]

    assert main(argv) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == (
        f"{asset} is not a valid verified asset: bad\\x1bkey: Extra inputs are not permitted"
    )
    assert not output.exists()


# --- every line a decision command stops with ----------------------------------------------


def _raise(error: BaseException) -> Any:
    def raising(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return raising


def _verify_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception) -> int:
    monkeypatch.setattr("auto_bi.decision.use_cases.verify", _raise(error))
    return main(_verify_argv(Case(tmp_path)))


def test_verify_says_its_usage_error_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _verify_error(tmp_path, monkeypatch, use_cases.VerifyError(RAW)) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == SAID


def test_verify_says_a_signature_error_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_bi.evidence.signing import EvidenceSignatureError

    error = EvidenceSignatureError("evidence.signature_key", RAW)
    assert _verify_error(tmp_path, monkeypatch, error) == EXIT_ERROR
    assert _one_line_error(capsys.readouterr().err) == SAID


def test_capture_says_its_usage_error_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "auto_bi.cli._result_capture_target",
        lambda _engine: (_raise(AssertionError()), "clickhouse://test", "auto_bi_ro"),
    )
    monkeypatch.setattr(
        "auto_bi.decision.use_cases.capture_result", _raise(use_cases.VerifyError(RAW))
    )
    argv = ["decision", "capture", str(tmp_path), "--candidate", str(tmp_path)]
    argv += ["--dwh", "clickhouse", "--output", str(tmp_path / "out.yaml")]

    assert main(argv) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == SAID


def _located(monkeypatch: pytest.MonkeyPatch) -> None:
    location = SimpleNamespace(store=object(), run_id="run-1")
    monkeypatch.setattr("auto_bi.decision.use_cases.locate_evidence", lambda *_args: location)


@pytest.mark.parametrize("kind", ["VerifyError", "EvidenceReadError", "EvidenceSignatureError"])
def test_evidence_says_its_usage_error_in_one_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    from auto_bi.evidence.inspect import EvidenceReadError
    from auto_bi.evidence.signing import EvidenceSignatureError

    if kind == "VerifyError":
        monkeypatch.setattr(
            "auto_bi.decision.use_cases.locate_evidence", _raise(use_cases.VerifyError(RAW))
        )
    else:
        _located(monkeypatch)
        error: Exception = (
            EvidenceReadError(RAW)
            if kind == "EvidenceReadError"
            else EvidenceSignatureError("evidence.signature_untrusted", RAW)
        )
        monkeypatch.setattr("auto_bi.evidence.inspect.inspect_bundle", _raise(error))

    assert main(["decision", "evidence", str(tmp_path)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == SAID


def test_evidence_says_an_unexpected_digest_in_one_line_with_exit_four(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_bi.evidence.inspect import EvidenceDigestError

    _located(monkeypatch)
    monkeypatch.setattr("auto_bi.evidence.inspect.inspect_bundle", _raise(EvidenceDigestError(RAW)))

    assert main(["decision", "evidence", str(tmp_path), "--expect-digest", "x"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == f"evidence.digest_unexpected: {SAID}"


def _artifact(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    _located(monkeypatch)
    artifact = SimpleNamespace(
        artifact_id=RAW, media_type="text/plain", size_bytes=len(payload), path="artifacts/x"
    )
    monkeypatch.setattr(
        "auto_bi.evidence.inspect.read_artifact", lambda *_args, **_kwargs: (artifact, payload)
    )


def test_evidence_says_an_artifact_is_not_text_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _artifact(monkeypatch, b"\xff\xfe")

    assert main(["decision", "evidence", str(tmp_path), "--artifact", "x"]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == (
        f"{SAID} is not text (text/plain, 2 bytes); it is stored at artifacts/x"
    )


def test_evidence_says_an_artifact_it_cannot_print_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _artifact(monkeypatch, "мера".encode())
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", stdout)

    assert main(["decision", "evidence", str(tmp_path), "--artifact", "x"]) == EXIT_ERROR
    assert _one_line_error(capsys.readouterr().err) == (
        f"{SAID} holds text the output stream cannot encode (ascii); it is stored at artifacts/x"
    )


def test_runs_says_why_it_cannot_list_in_one_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`decision runs` says it without `error: `, as before; the line is one all the same."""
    from auto_bi.decision.runs import RunDiscoveryError

    monkeypatch.setattr(
        "auto_bi.decision.runs.FilesystemRunRepository.list_runs",
        _raise(RunDiscoveryError(RAW)),
    )

    assert main(["decision", "runs", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(f"error: {captured.err}") == SAID


def test_investigate_says_its_refusal_in_one_line_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_bi.investigation.application import OfflineInvestigationError

    monkeypatch.setattr(
        "auto_bi.investigation.application.write_offline_investigation",
        _raise(OfflineInvestigationError(RAW)),
    )
    argv = ["decision", "investigate", str(tmp_path / "plan.yaml"), "--results", "r.yaml"]

    assert main([*argv, "--output", str(tmp_path / "out")]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _one_line_error(captured.err) == SAID


def test_a_line_the_stream_cannot_encode_is_spelled_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A printable character the stream has no form for is spelled by its escape there."""
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="\n")
    monkeypatch.setattr(sys, "stderr", stderr)

    assert _verify_error(tmp_path, monkeypatch, use_cases.VerifyError(RAW)) == EXIT_ERROR
    stderr.flush()
    written = stderr.buffer.getvalue().decode("ascii")  # type: ignore[attr-defined]
    assert (
        written
        == "error: " + SAID.replace("ме\u0301ра", "\\u043c\\u0435\\u0301\\u0440\\u0430") + "\n"
    )


def test_the_versions_of_evidence_are_at_least_those_this_was_written_under() -> None:
    """The line is printed and never kept: no report, check or bundle changes with it."""
    assert int(VERIFIER_VERSION) >= 114
    assert int(CHECK_VERSION) >= 77


# argparse's own refusal of a decision command -------------------------------------------------

#: RAW without its spaces. argparse takes an argument that holds a space and names no option of
#: its parser, in full or in part, for a positional, whatever it begins with, so an option is
#: written with none.
BARE = RAW.replace(" ", "")
#: BARE, as the line spells it.
BARE_SAID = SAID.replace(" ", "")


def _argparse_refusal(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> str:
    """Standard error of a command line argparse refuses, with exit 2 and no standard output.

    Both streams take every character as it is, as a stream an application hands over may.
    """
    out, err = io.StringIO(), io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err)
        with pytest.raises(SystemExit) as ended:
            main(argv)
    assert ended.value.code == 2
    assert out.getvalue() == ""
    return err.getvalue()


def _usage(monkeypatch: pytest.MonkeyPatch, *command: str) -> str:
    """The usage lines of *command*, which its `--help` begins with."""
    out = io.StringIO()
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        with pytest.raises(SystemExit) as ended:
            main([*command, "--help"])
    assert ended.value.code == 0
    return out.getvalue().split("\n\n", 1)[0] + "\n"


@pytest.mark.parametrize(
    ("argv", "quoted"),
    [
        pytest.param(["decision", "runs", "evidence", RAW], SAID, id="argument"),
        pytest.param(["decision", "runs", "evidence", "--" + BARE], "--" + BARE_SAID, id="option"),
        pytest.param(
            ["decision", "--" + BARE, "runs", "evidence"], "--" + BARE_SAID, id="before-the-command"
        ),
        pytest.param(
            ["--" + BARE, "decision", "runs", "evidence"], "--" + BARE_SAID, id="before-decision"
        ),
    ],
)
def test_an_argument_a_decision_command_does_not_take_is_spelled(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], quoted: str
) -> None:
    """argparse quotes it after `unrecognized arguments: `, below the usage lines, as before."""
    assert _argparse_refusal(monkeypatch, argv) == (
        _usage(monkeypatch) + f"auto_bi: error: unrecognized arguments: {quoted}\n"
    )


@pytest.mark.parametrize(
    ("command", "prefix", "matches"),
    [
        ("verify", "--d", "--dashboards, --dwh"),
        (
            "capture",
            "--c",
            "--candidate, --candidate-semantic-manifest, --candidate-dbt-database, --captured-at",
        ),
    ],
)
def test_an_option_that_could_be_two_is_spelled(
    monkeypatch: pytest.MonkeyPatch, command: str, prefix: str, matches: str
) -> None:
    """The command quotes the option it cannot tell, below its own usage lines, as before."""
    assert _argparse_refusal(monkeypatch, ["decision", command, f"{prefix}={BARE}"]) == (
        _usage(monkeypatch, "decision", command)
        + f"auto_bi decision {command}: error: ambiguous option: {prefix}={BARE_SAID}"
        + f" could match {matches}\n"
    )


def test_an_option_auto_bi_may_refuse_ahead_of_the_command_is_spelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--=` could be `--help` or `--version` of `auto_bi`, and `--help` or `--json` of `runs`.

    CPython 3.12.0 and 3.12.6 refuse it in the parser of `auto_bi`, ahead of `decision`; 3.12.7
    and later, in the parser of `runs`. Either line is spelled.
    """
    err = _argparse_refusal(monkeypatch, ["decision", "runs", "evidence", "--=" + BARE])
    said = f"error: ambiguous option: --={BARE_SAID} could match --help"
    assert err in (
        _usage(monkeypatch) + f"auto_bi: {said}, --version\n",
        _usage(monkeypatch, "decision", "runs") + f"auto_bi decision runs: {said}, --json\n",
    )


@pytest.mark.parametrize(
    ("argv", "command", "line"),
    [
        pytest.param(
            ["decision", "runs", "evidence", "мера"],
            (),
            "auto_bi: error: unrecognized arguments: мера",
            id="argument",
        ),
        pytest.param(
            ["decision", "--мера", "runs", "evidence"],
            (),
            "auto_bi: error: unrecognized arguments: --мера",
            id="before-the-command",
        ),
        pytest.param(
            ["--мера", "decision", "runs", "evidence"],
            (),
            "auto_bi: error: unrecognized arguments: --мера",
            id="before-decision",
        ),
        pytest.param(
            ["decision", "verify", "--d=мера"],
            ("decision", "verify"),
            "auto_bi decision verify: error: ambiguous option: --d=мера could match"
            " --dashboards, --dwh",
            id="ambiguous",
        ),
    ],
)
def test_a_printable_argument_is_refused_as_before(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], command: tuple[str, ...], line: str
) -> None:
    assert _argparse_refusal(monkeypatch, argv) == _usage(monkeypatch, *command) + line + "\n"


@pytest.mark.parametrize(
    ("argv", "quoted"),
    [
        pytest.param(["gaps", "--" + BARE], "--" + BARE, id="after-a-v1-command"),
        pytest.param(["--" + BARE, "gaps"], "--" + BARE, id="before-a-v1-command"),
        pytest.param(
            ["gaps", "decision", "--" + BARE],
            "decision --" + BARE,
            id="decision-after-a-v1-command",
        ),
    ],
)
def test_argparse_outside_decision_refuses_as_it_did(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], quoted: str
) -> None:
    """A command line of the v1 runtime quotes it as it is, wherever it is written."""
    assert _argparse_refusal(monkeypatch, argv) == (
        _usage(monkeypatch) + f"auto_bi: error: unrecognized arguments: {quoted}\n"
    )


def test_a_choice_argparse_refuses_is_quoted_as_python_quotes_a_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argparse quotes the value itself, its backslash doubled, as it did before."""
    err = _argparse_refusal(monkeypatch, ["decision", RAW])
    usage = _usage(monkeypatch, "decision")
    assert err.startswith(usage)
    line = err[len(usage) :]
    assert line.startswith(
        f"auto_bi decision: error: argument decision_command: invalid choice: {RAW!r} (choose from "
    )
    assert line.endswith(")\n")
    assert line.count("\n") == 1
    assert all(char.isprintable() for char in line[:-1])


def test_an_argument_argparse_takes_for_the_command_is_quoted_as_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-` and RAW holds a space and names no option, so argparse takes it for the command.

    Ahead of `decision` too: it is quoted as Python quotes a string, as before.
    """
    refused = "-" + RAW
    err = _argparse_refusal(monkeypatch, [refused, "decision", "runs", "evidence"])
    usage = _usage(monkeypatch)
    assert err.startswith(usage)
    line = err[len(usage) :]
    assert line.startswith(
        f"auto_bi: error: argument command: invalid choice: {refused!r} (choose "
    )
    assert line.endswith(")\n")
    assert line.count("\n") == 1


def test_an_argparse_refusal_on_a_stream_that_encodes_ascii(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What is spelled is ASCII; a printable letter the stream has no form for is escaped."""
    stderr = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="\n")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", io.StringIO())
        patch.setattr(sys, "stderr", stderr)
        with pytest.raises(SystemExit) as ended:
            main(["decision", "runs", "evidence", RAW])
    assert ended.value.code == 2
    stderr.flush()
    written = stderr.buffer.getvalue().decode("ascii")  # type: ignore[attr-defined]
    said = _usage(monkeypatch) + f"auto_bi: error: unrecognized arguments: {SAID}\n"
    assert written == said.encode("ascii", "backslashreplace").decode("ascii")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows hands a program its arguments as text")
def test_a_byte_of_argv_no_character_decodes_is_spelled_by_a_process(tmp_path: Path) -> None:
    """On POSIX Python reads an argument byte UTF-8 has no character for as a lone surrogate."""
    ended = subprocess.run(
        [
            sys.executable,
            "-m",
            "auto_bi.cli",
            "decision",
            "runs",
            str(tmp_path),
            b"--x" + bytes([0x80]) + b"y",
        ],
        capture_output=True,
        env=dict(os.environ, PYTHONUTF8="1"),
        check=False,
        timeout=30,
    )
    assert ended.returncode == 2
    assert ended.stdout == b""
    backslash = bytes([92])
    assert ended.stderr.endswith(
        b"\nauto_bi: error: unrecognized arguments: --x" + backslash + b"udc80y\n"
    )
