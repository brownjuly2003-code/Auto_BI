"""A decision command answers on the stream it is given, whatever that stream encodes.

What a decision command prints holds what it found — a path, an id, a detail a check wrote —
and a stream refuses a character it has no form for. The commands printed with nothing
between them and the stream. With the fixture under a directory named in Cyrillic and
streams that encode ASCII, `decision validate` ended in `UnicodeEncodeError` once an issue
named a file; `decision verify` while its checks were printed, at a detail that holds a
section sign and after the bundle was sealed; `decision capture` and `decision investigate`
after their files were written; `--json` printed no document. The exit code was 1, which
is no verdict (`docs/specs/unencodable-output.md`).
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auto_bi import cli
from auto_bi.cli import main
from auto_bi.evidence.model import ClaimType
from auto_bi.verification.model import EXIT_ERROR
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import ASSET_ID, CREATED_AT
from tests.test_evidence_store import RUN, writer
from tests.test_investigation_cli import PLAN_FIXTURE, RESULT_FIXTURE
from tests.test_stated_run_facts import _Engine
from tests.test_unreadable_run_inputs import _assets, _case

BS = chr(92)
NL = chr(10)
ARROW = chr(0x2192)
SECTION = chr(0xA7)
DASH = chr(0x2014)
#: Half of the UTF-16 form of a character: no character, and no UTF-8 form.
LONE = chr(0xD800)
#: What a byte of a command line that is no UTF-8 is decoded to (`surrogateescape`).
UNDECODED = chr(0xDCFF)
#: A character outside the basic plane: one escape of eight digits, two of four in JSON.
ASTRAL = chr(0x1F600)

#: The fixture lives under a directory only UTF-8 has every character of: Cyrillic, which
#: cp1251 encodes and the other three do not, and an arrow, which none of the four encodes.
HOME = "данные" + ARROW + "v2"
#: Streams that encode less than UTF-8, as a redirected one does by its locale.
NARROW = ["ascii", "latin-1", "cp1252", "cp1251"]
ENCODINGS = [*NARROW, "utf-8"]

CAVEAT = "проверено вручную " + ARROW + " " + SECTION + "8.4"
#: Text artifacts of the sealed bundle, by what their payload holds.
NOTES = {
    "note-plain": "0.412 -> 0.377",
    "note-cyrillic": "Москва: 0.412",
    "note-arrow": "0.412 " + ARROW + " 0.377",
}

COMMANDS = {
    "_decision_validate",
    "_decision_verify",
    "_decision_capture",
    "_decision_evidence",
    "_decision_investigate",
}


# --- what a stream shows, said without the code under test ------------------------------


def _escaped(char: str) -> str:
    """The backslash escape of one character, written out here and not asked of a codec."""
    code = ord(char)
    if code <= 0xFF:
        return f"{BS}x{code:02x}"
    if code <= 0xFFFF:
        return f"{BS}u{code:04x}"
    return f"{BS}U{code:08x}"


def _encodes(encoding: str, text: str) -> bool:
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _shown(text: str, encoding: str) -> str:
    """*text* as a stream of *encoding* shows it: what it encodes as it is, the rest escaped."""
    return "".join(char if _encodes(encoding, char) else _escaped(char) for char in text)


def _json_ascii(text: str) -> str:
    """The text of a JSON document with each character outside ASCII as JSON escapes it."""
    units: list[str] = []
    for char in text:
        code = ord(char)
        if code < 0x80:
            units.append(char)
        elif code <= 0xFFFF:
            units.append(f"{BS}u{code:04x}")
        else:
            high, low = divmod(code - 0x10000, 0x400)
            units.append(f"{BS}u{0xD800 + high:04x}{BS}u{0xDC00 + low:04x}")
    return "".join(units)


class _Stream:
    """A text stream over bytes that refuses what its encoding has no form for."""

    def __init__(self, encoding: str, errors: str = "strict") -> None:
        self.raw = io.BytesIO()
        self.text = io.TextIOWrapper(self.raw, encoding=encoding, errors=errors, newline="")
        self.encoding = encoding
        self.errors = errors

    def said(self) -> str:
        self.text.flush()
        return self.raw.getvalue().decode(self.encoding, self.errors)


class _Refusing:
    """A stream that takes ASCII alone and names no encoding."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        text.encode("ascii")
        self.written.append(text)
        return len(text)

    def said(self) -> str:
        return "".join(self.written)


@dataclass(frozen=True)
class _Answer:
    code: int
    out: str
    err: str
    #: Every file the run left where it writes, by its path there.
    wrote: dict[str, bytes] = field(default_factory=dict)


def _run(argv: list[str], encoding: str = "") -> tuple[int, str, str]:
    """Run a command on streams of *encoding*; with none named, on streams that take any text.

    The second is the answer as the command meant it: UTF-8 that passes a lone surrogate.
    """
    if encoding:
        out, err = _Stream(encoding), _Stream(encoding)
    else:
        out, err = _Stream("utf-8", "surrogatepass"), _Stream("utf-8", "surrogatepass")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out.text)
        patch.setattr(sys, "stderr", err.text)
        code = main(argv)
    return code, out.said(), err.said()


def _flag(argv: list[str], flag: str, value: Path) -> list[str]:
    changed = list(argv)
    changed[changed.index(flag) + 1] = str(value)
    return changed


def _seal(root: Path) -> Path:
    """A bundle that verifies, with a caveat and three notes a reader asks for."""
    builder = writer(root)
    builder.add_check("freshness", {"status": "pass", "summary_code": "freshness.within_max_age"})
    refs = [
        builder.add_blob(
            name, kind="raw_payload", payload=text.encode("utf-8"), media_type="text/plain"
        )
        for name, text in NOTES.items()
    ]
    builder.add_claim(
        claim_id="margin-drop",
        claim_type=ClaimType.OBSERVATION,
        summary_code="margin.candidate_differs",
        evidence_refs=[*refs, "check-freshness"],
        caveats=[CAVEAT],
    )
    builder.write()
    return root / RUN


class _Stand:
    """One fixture every decision command runs over, under a directory named `HOME`."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path / HOME
        self.home.mkdir()
        self.case = _case(self.home)
        #: Where a run writes. Emptied before each run, so that every run starts alike.
        self.out = self.home / "out"
        #: A path nothing is at: a command that is refused by it names it.
        self.missing = self.home / "нет"
        #: A directory with one file that is no contract, named as `HOME` is: `decision
        #: validate` names the file by the place of the path it was found under and its own
        #: name, `$2/<HOME>.yaml`, and nothing of the path (validated-path-names).
        self.notes = self.home / "notes"
        self.notes.mkdir()
        (self.notes / f"{HOME}.yaml").write_text("- not a mapping\n", encoding="utf-8")
        self.sealed = _seal(self.home / "sealed")
        monkeypatch.setattr(cli, "_result_capture_target", _Engine().target)

    def run(self, argv: list[str], encoding: str = "") -> _Answer:
        shutil.rmtree(self.out, ignore_errors=True)
        self.out.mkdir()
        code, out, err = _run(argv, encoding)
        wrote = {
            path.relative_to(self.out).as_posix(): path.read_bytes()
            for path in sorted(self.out.rglob("*"))
            if path.is_file()
        }
        return _Answer(code, out, err, wrote)

    def validate(self, *extra: str) -> list[str]:
        return ["decision", "validate", str(self.case.contracts), str(self.notes), *extra]

    def verify(self, *extra: str) -> list[str]:
        return _flag(_verify_argv(self.case, *extra), "--evidence", self.out / "evidence")

    def capture(self) -> list[str]:
        argv = ["decision", "capture", str(_assets(self.case))]
        argv += ["--candidate", str(self.case.candidate), "--asset", ASSET_ID]
        argv += ["--dwh", "clickhouse", "--captured-at", CREATED_AT]
        return [*argv, "--output", str(self.out / "captured.yaml")]

    def investigate(self, plan: Path = PLAN_FIXTURE) -> list[str]:
        argv = ["decision", "investigate", str(plan), "--results", str(RESULT_FIXTURE)]
        return [*argv, "--output", str(self.out / "brief")]

    def evidence(self, *extra: str) -> list[str]:
        return ["decision", "evidence", str(self.sealed), *extra]


#: Answers in lines: the command, the exit code it ends with on any stream, and a piece of
#: the answer that ASCII has no form for.
LINES: dict[str, tuple[Callable[[_Stand], list[str]], int, str]] = {
    "validate, an issue names a path": (lambda stand: stand.validate(), 4, HOME),
    "verify, a run that passes": (lambda stand: stand.verify(), 0, SECTION),
    "verify, refused by a path": (
        lambda stand: _flag(stand.verify(), "--base", stand.missing),
        EXIT_ERROR,
        HOME,
    ),
    "capture, an output path": (lambda stand: stand.capture(), 0, HOME),
    "capture, refused by a path": (
        lambda stand: _flag(stand.capture(), "--candidate", stand.missing),
        EXIT_ERROR,
        HOME,
    ),
    "investigate, an output path": (lambda stand: stand.investigate(), 0, HOME),
    "investigate, refused by a path": (
        lambda stand: stand.investigate(stand.missing),
        EXIT_ERROR,
        HOME,
    ),
    "evidence, a caveat": (lambda stand: stand.evidence(), 0, CAVEAT),
    "evidence, refused by a path": (
        lambda stand: ["decision", "evidence", str(stand.missing)],
        EXIT_ERROR,
        HOME,
    ),
}
#: Answers that are one JSON document.
DOCUMENTS: dict[str, tuple[Callable[[_Stand], list[str]], int, str]] = {
    "validate --json": (lambda stand: stand.validate("--json"), 4, HOME),
    "verify --json": (lambda stand: stand.verify("--json"), 0, SECTION),
    "evidence --json": (lambda stand: stand.evidence("--json"), 0, CAVEAT),
}


# --- one line -----------------------------------------------------------------------------

#: A stream, a line, and the line as that stream shows it.
SHOWN = [
    pytest.param("ascii", "plain", "plain", id="ASCII takes ASCII"),
    pytest.param("ascii", SECTION + "8.2", BS + "xa7" + "8.2", id="ASCII, a section sign"),
    pytest.param("ascii", "д", BS + "u0434", id="ASCII, a Cyrillic letter"),
    pytest.param("ascii", ASTRAL, BS + "U0001f600", id="ASCII, outside the basic plane"),
    pytest.param("latin-1", SECTION + DASH, SECTION + BS + "u2014", id="Latin-1, a dash"),
    pytest.param("cp1252", "д" + DASH, BS + "u0434" + DASH, id="cp1252, a Cyrillic letter"),
    pytest.param("cp1251", "д" + ARROW, "д" + BS + "u2192", id="cp1251, an arrow"),
    pytest.param("utf-8", "д" + ARROW + ASTRAL, "д" + ARROW + ASTRAL, id="UTF-8 takes them"),
    pytest.param("utf-8", "a" + LONE + "b", "a" + BS + "ud800" + "b", id="UTF-8, a surrogate"),
    pytest.param("utf-8", UNDECODED, BS + "udcff", id="UTF-8, a byte that is no UTF-8"),
    pytest.param("ascii", "a" + BS + "b", "a" + BS + "b", id="a backslash stays one"),
    pytest.param("ascii", "", "", id="an empty line"),
]


@pytest.mark.parametrize(("encoding", "line", "shown"), SHOWN)
def test_a_line_is_printed_with_an_escape_for_what_the_stream_cannot_encode(
    encoding: str, line: str, shown: str
) -> None:
    stream = _Stream(encoding)

    cli._say(line, file=stream.text)

    assert stream.said() == shown + NL
    # What the command tests below expect is what is written out here.
    assert _shown(line, encoding) == shown


def test_a_line_goes_to_standard_output_unless_a_stream_is_named() -> None:
    out, err = _Stream("ascii"), _Stream("ascii")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out.text)
        patch.setattr(sys, "stderr", err.text)
        cli._say("д: out")
        cli._say("д: err", file=err.text)
        cli._say("plain: err", file=err.text)

    assert out.said() == BS + "u0434: out" + NL
    assert err.said() == BS + "u0434: err" + NL + "plain: err" + NL


def test_a_stream_that_takes_every_character_gets_the_line_as_it_is() -> None:
    """A stream that names no encoding is not one that encodes ASCII: it is asked first."""
    stream = io.StringIO()

    cli._say("д" + ARROW + SECTION + LONE, file=stream)

    assert stream.getvalue() == "д" + ARROW + SECTION + LONE + NL


@pytest.mark.parametrize("named", [False, True], ids=["no encoding", "an encoding of None"])
def test_a_stream_that_refuses_and_names_no_encoding_is_printed_to_in_ascii(named: bool) -> None:
    stream = _Refusing()
    if named:
        stream.encoding = None  # type: ignore[attr-defined]

    cli._say("д" + SECTION + " plain", file=stream)  # type: ignore[arg-type]

    assert stream.said() == BS + "u0434" + BS + "xa7" + " plain" + NL


# --- one JSON document --------------------------------------------------------------------

DOCUMENT: dict[str, Any] = {"город": "Москва" + ARROW, "n": [1, SECTION + "8.2", ASTRAL]}
#: The document as it is printed where every character of it has a form.
PRINTED = NL.join(
    [
        "{",
        '  "город": "Москва' + ARROW + '",',
        '  "n": [',
        "    1,",
        '    "' + SECTION + '8.2",',
        '    "' + ASTRAL + '"',
        "  ]",
        "}",
        "",
    ]
)
#: The same document where one has none: each character outside ASCII as JSON escapes it.
PRINTED_ASCII = NL.join(
    [
        "{",
        '  "' + "".join(f"{BS}u{ord(char):04x}" for char in "город") + '": "'
        # Each of these is one letter of the six.
        + "".join(f"{BS}u{code:04x}" for code in (0x41C, 0x43E, 0x441, 0x43A, 0x432, 0x430))
        + BS
        + 'u2192",',
        '  "n": [',
        "    1,",
        '    "' + BS + 'u00a78.2",',
        '    "' + BS + "ud83d" + BS + 'ude00"',
        "  ]",
        "}",
        "",
    ]
)


def _say_json(document: Any, encoding: str) -> str:
    out, err = _Stream(encoding), _Stream(encoding)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out.text)
        patch.setattr(sys, "stderr", err.text)
        cli._say_json(document)
    assert err.said() == ""
    return out.said()


def test_a_document_is_printed_as_it_is_where_the_stream_encodes_it() -> None:
    assert _say_json(DOCUMENT, "utf-8") == PRINTED
    assert _say_json({"n": [1, SECTION + "8.2"]}, "latin-1") == NL.join(
        ["{", '  "n": [', "    1,", '    "' + SECTION + '8.2"', "  ]", "}", ""]
    )


@pytest.mark.parametrize("encoding", NARROW)
def test_a_document_is_printed_in_ascii_where_the_stream_refuses_it(encoding: str) -> None:
    """All of it, a letter the stream does encode too: one document, one spelling."""
    printed = _say_json(DOCUMENT, encoding)

    assert printed == PRINTED_ASCII
    assert json.loads(printed) == DOCUMENT
    # The two oracles of this module agree on it.
    assert _json_ascii(PRINTED) == PRINTED_ASCII


@pytest.mark.parametrize("char", [LONE, UNDECODED], ids=["a lone surrogate", "a byte"])
def test_a_document_with_a_lone_surrogate_is_printed_on_a_stream_of_utf8(char: str) -> None:
    document = {"path": "a" + char}

    printed = _say_json(document, "utf-8")

    assert printed == NL.join(["{", '  "path": "a' + f"{BS}u{ord(char):04x}" + '"', "}", ""])
    assert json.loads(printed) == document


# --- the five commands --------------------------------------------------------------------


@pytest.mark.parametrize("name", [*LINES, *DOCUMENTS])
def test_each_answer_holds_what_ascii_has_no_form_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """The answers below are no answers a stream of ASCII takes as they are."""
    stand = _Stand(tmp_path, monkeypatch)
    build, code, piece = {**LINES, **DOCUMENTS}[name]

    meant = stand.run(build(stand))

    assert meant.code == code
    assert piece in meant.out + meant.err
    assert not piece.isascii()


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("name", LINES)
def test_a_command_answers_on_a_stream_that_encodes_less(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, encoding: str
) -> None:
    stand = _Stand(tmp_path, monkeypatch)
    build, code, _piece = LINES[name]
    meant = stand.run(build(stand))

    said = stand.run(build(stand), encoding)

    assert said.code == code
    assert said.out == _shown(meant.out, encoding)
    assert said.err == _shown(meant.err, encoding)
    assert said.wrote == meant.wrote


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("name", DOCUMENTS)
def test_a_json_answer_is_the_same_document_on_a_stream_that_encodes_less(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, encoding: str
) -> None:
    stand = _Stand(tmp_path, monkeypatch)
    build, code, _piece = DOCUMENTS[name]
    meant = stand.run(build(stand))

    said = stand.run(build(stand), encoding)

    printed = meant.out if _encodes(encoding, meant.out) else _json_ascii(meant.out)
    assert (said.code, said.out, said.err) == (code, printed, "")
    assert json.loads(said.out) == json.loads(meant.out)
    assert said.wrote == meant.wrote


def test_the_directory_is_spelled_as_each_stream_can(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One answer, written out: ASCII escapes every letter of it, cp1251 the arrow alone."""
    stand = _Stand(tmp_path, monkeypatch)
    letters = "".join(f"{BS}u{code:04x}" for code in (0x434, 0x430, 0x43D, 0x43D, 0x44B, 0x435))

    in_ascii = stand.run(stand.capture(), "ascii")
    in_cp1251 = stand.run(stand.capture(), "cp1251")

    assert letters + BS + "u2192v2" in in_ascii.out.splitlines()[0]
    assert "данные" + BS + "u2192v2" in in_cp1251.out.splitlines()[0]
    assert in_ascii.out.splitlines()[0].startswith("wrote    ")
    assert (in_ascii.code, in_cp1251.code) == (0, 0)
    assert in_ascii.wrote == in_cp1251.wrote != {}


@pytest.mark.parametrize("char", [UNDECODED, LONE], ids=["a byte", "a lone surrogate"])
@pytest.mark.parametrize("as_json", [False, True], ids=["lines", "json"])
def test_a_path_with_a_lone_surrogate_is_named_on_a_stream_of_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool, char: str
) -> None:
    """No encoding has a form for it: UTF-8 refuses it as ASCII refuses a letter. The path is a
    file's below a validated directory, named as the directory listed it; a filesystem that
    takes no such name — a byte that is no UTF-8 on macOS, U+D800 on POSIX — has no such file."""
    stand = _Stand(tmp_path, monkeypatch)
    below = stand.home / "below"
    below.mkdir()
    try:
        (below / f"no{char}.yaml").write_text("- not a mapping\n", encoding="utf-8")
    except (OSError, UnicodeError) as refused:
        pytest.skip(f"the filesystem takes no such name: {type(refused).__name__}")
    argv = ["decision", "validate", str(stand.case.contracts), str(below)]
    argv += ["--json"] if as_json else []
    meant = stand.run(argv)

    said = stand.run(argv, "utf-8")

    named = f"$2/no{char}.yaml"
    assert (said.code, said.err) == (4, "")
    if as_json:
        assert named in [issue["source"] for issue in json.loads(meant.out)["issues"]]
        assert char in meant.out
        assert said.out.isascii()
        assert json.loads(said.out) == json.loads(meant.out)
    else:
        # A line that names what the command read spells it before a stream is asked
        # (docs/specs/one-line-answers.md), so a stream of any encoding gets the escape.
        assert char not in meant.out
        assert said.out == meant.out
        assert f"$2/no{BS}u{ord(char):04x}.yaml" in said.out


@pytest.mark.parametrize("char", [UNDECODED, LONE], ids=["a byte", "a lone surrogate"])
@pytest.mark.parametrize("as_json", [False, True], ids=["lines", "json"])
def test_a_validated_path_with_a_lone_surrogate_is_no_part_of_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool, char: str
) -> None:
    """A validated path is named by its place, `$2`, and nothing of its text is printed."""
    stand = _Stand(tmp_path, monkeypatch)
    argv = ["decision", "validate", str(stand.case.contracts), str(stand.home / ("no" + char))]
    argv += ["--json"] if as_json else []
    meant = stand.run(argv)

    said = stand.run(argv, "utf-8")

    assert char not in meant.out
    assert "$2" in meant.out
    assert (said.code, said.out, said.err) == (4, meant.out, "")


# --- one artifact -------------------------------------------------------------------------

#: An artifact and a stream that encodes all of its text.
PRINTED_ARTIFACTS = [
    *(("note-plain", encoding) for encoding in ENCODINGS),
    ("note-cyrillic", "cp1251"),
    ("note-cyrillic", "utf-8"),
    ("note-arrow", "utf-8"),
]
#: An artifact and a stream that has no form for a character of it.
REFUSED_ARTIFACTS = [
    ("note-cyrillic", "ascii"),
    ("note-cyrillic", "latin-1"),
    ("note-cyrillic", "cp1252"),
    *(("note-arrow", encoding) for encoding in NARROW),
]


@pytest.mark.parametrize(("artifact", "encoding"), PRINTED_ARTIFACTS)
def test_an_artifact_the_stream_encodes_is_printed_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str, encoding: str
) -> None:
    stand = _Stand(tmp_path, monkeypatch)

    said = stand.run(stand.evidence("--artifact", artifact), encoding)

    assert said == _Answer(0, NOTES[artifact] + NL, "")


@pytest.mark.parametrize(("artifact", "encoding"), REFUSED_ARTIFACTS)
def test_an_artifact_the_stream_cannot_encode_is_not_printed_with_escapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str, encoding: str
) -> None:
    """The payload is the evidence: with an escape in it, it is other text. It gets an address."""
    stand = _Stand(tmp_path, monkeypatch)
    stored = "blobs/sha256/" + hashlib.sha256(NOTES[artifact].encode("utf-8")).hexdigest()

    said = stand.run(stand.evidence("--artifact", artifact), encoding)

    refusal = (
        f"error: {artifact} holds text the output stream cannot encode ({encoding}); "
        f"it is stored at {stored}"
    )
    assert said == _Answer(EXIT_ERROR, "", refusal + NL)
    assert (stand.sealed / stored).read_bytes() == NOTES[artifact].encode("utf-8")


def test_an_artifact_is_refused_by_a_stream_that_names_no_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The encoding named is the one of the stream that refused, where it names one."""
    stand = _Stand(tmp_path, monkeypatch)
    out, err = _Refusing(), _Stream("utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err.text)
        code = main(stand.evidence("--artifact", "note-arrow"))

    assert (code, out.said()) == (EXIT_ERROR, "")
    assert err.said().startswith(
        "error: note-arrow holds text the output stream cannot encode (an unnamed encoding); "
    )


# --- every line of the five ---------------------------------------------------------------


def test_no_decision_command_prints_past_the_one_function() -> None:
    """A line that is ASCII today names a path tomorrow: none is printed around `_say`."""
    tree = ast.parse(Path(cli.__file__).read_text(encoding="utf-8"))
    prints = {
        node.name: [
            ast.unparse(call)
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "print"
        ]
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_decision_")
    }

    # The settings helper introduced by T-68 and the run catalogue introduced by T-106
    # follow the same output boundary.
    assert set(prints) == COMMANDS | {"_decision_settings", "_decision_runs"}
    # The one payload of an artifact, which is printed as it is or not at all.
    assert {name: calls for name, calls in prints.items() if calls} == {
        "_decision_evidence": ["print(text)"]
    }


# --- the commands as processes ------------------------------------------------------------


def _process(argv: list[str], encoding: str) -> subprocess.CompletedProcess[bytes]:
    """The command as a process of its own: the streams are the interpreter's."""
    root = Path(cli.__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONIOENCODING=encoding)
    return subprocess.run(
        [sys.executable, "-m", "auto_bi.cli", *argv], capture_output=True, env=env, check=False
    )


def test_a_process_whose_streams_encode_ascii_exits_by_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = _Stand(tmp_path, monkeypatch)
    meant = stand.run(stand.validate())

    ended = _process(stand.validate(), "ascii")

    assert (ended.returncode, ended.stderr) == (4, b"")
    assert ended.stdout.decode("ascii").splitlines() == _shown(meant.out, "ascii").splitlines()


def test_a_process_whose_streams_encode_ascii_prints_its_json_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stand = _Stand(tmp_path, monkeypatch)
    meant = stand.run(stand.verify("--json"))

    ended = _process(stand.verify("--json"), "ascii")

    assert (ended.returncode, ended.stderr) == (0, b"")
    assert json.loads(ended.stdout.decode("ascii")) == json.loads(meant.out)
    assert SECTION in meant.out
