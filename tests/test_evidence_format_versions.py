"""The record of what each evidence format version changed stays in step with the code.

`docs/specs/evidence-format-versions.md` holds an entry for every verifier and check version from
the first recorded one up to the constant, newest first, each in its own section;
`release_gate.py` keeps only the newest few of them beside the constants, and those read the same
in both places. Entries older than the code's window are history: their text is pinned.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from auto_bi.verification import release_gate
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "docs" / "specs" / "evidence-format-versions.md"
CODE = Path(release_gate.__file__)

#: (constant, heading word in the spec, first recorded version, current version)
VERSIONS = (
    ("VERIFIER_VERSION", "Verifier", 11, int(VERIFIER_VERSION)),
    ("CHECK_VERSION", "Check", 7, int(CHECK_VERSION)),
)
MOST_KEPT_IN_CODE = 5
#: Per heading word: entries up to this version and the sha256 of their text (`history_digest`).
#: The bound sits right below the code's window, so an entry the code lets go is pinned at once;
#: an edit to an old entry is a decision to update the digest here, not a slip.
FROZEN = {
    "Verifier": (128, "7e663c62c006a47b6bfe43325c8a80f19b58d1dd64a8fdde3699f461e473136c"),
    "Check": (80, "20cd547a0b4760023412e74340f5fbd9d75e18cf2247c6a966c36ffefb05abd7"),
}
_CODE_ENTRY = re.compile(r"^#: (\d+) — (.*)$")


def _words(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())


def spec_sections(text: str) -> dict[str, list[str]]:
    """The lines of each `## <word> version` section, in the order written.

    Before the first section only the title may be a heading; a malformed or repeated section
    heading is an error, not a section to skip.
    """
    sections: dict[str, list[str]] = {}
    body: list[str] | None = None
    for number, line in enumerate(text.split("\n")):
        if line.startswith("## "):
            m = re.fullmatch(r"## (\w+) version", line)
            if m is None or m.group(1) in sections:
                raise ValueError(f"unexpected section heading {line!r}")
            body = sections[m.group(1)] = []
        elif body is not None:
            body.append(line)
        elif line.startswith("#") and number > 0:
            raise ValueError(f"a heading before the first section: {line!r}")
    return sections


def spec_entries(text: str, word: str) -> list[tuple[int, str]]:
    """(version, text) of every entry of the `## <word> version` section, in the order written.

    Inside the section every line starting with `#` must be a `### <word> <n>` heading: anything
    else would end an entry early or file it under the wrong constant.
    """
    sections = spec_sections(text)
    if word not in sections:
        raise ValueError(f"no '## {word} version' section")
    entries: list[tuple[int, list[str]]] = []
    for line in sections[word]:
        if line.startswith("#"):
            m = re.fullmatch(rf"### {word} (\d+)", line)
            if m is None:
                raise ValueError(f"'## {word} version': unexpected line {line!r}")
            entries.append((int(m.group(1)), []))
        elif entries:
            entries[-1][1].append(line)
    return [(n, _words(body)) for n, body in entries]


def history_digest(entries: list[tuple[int, str]], up_to: int) -> str:
    """sha256 of the entries up to a version; wrapping and spacing do not count."""
    kept = "\n".join(f"{n} {body}" for n, body in entries if n <= up_to)
    return hashlib.sha256(kept.encode("utf-8")).hexdigest()


def code_entries(source: str, constant: str) -> list[tuple[int, str]]:
    """(version, text) of every `#: <n> — ` entry in the comment right above the constant."""
    lines = source.split("\n")
    at = next(i for i, line in enumerate(lines) if line.startswith(f"{constant} = "))
    start = at
    while start > 0 and lines[start - 1].startswith("#:"):
        start -= 1
    entries: list[tuple[int, list[str]]] = []
    for line in lines[start:at]:
        m = _CODE_ENTRY.match(line)
        if m:
            entries.append((int(m.group(1)), [m.group(2)]))
        elif entries:
            entries[-1][1].append(line[2:])
    return [(n, _words(body)) for n, body in entries]


def test_the_spec_has_one_section_per_constant() -> None:
    sections = spec_sections(SPEC.read_text(encoding="utf-8"))
    assert list(sections) == [word for _, word, _, _ in VERSIONS]


@pytest.mark.parametrize(("constant", "word", "first", "current"), VERSIONS)
def test_the_spec_records_every_version_once_newest_first(
    constant: str, word: str, first: int, current: int
) -> None:
    entries = spec_entries(SPEC.read_text(encoding="utf-8"), word)
    assert [n for n, _ in entries] == list(range(current, first - 1, -1)), constant
    assert all(body for _, body in entries), f"{constant}: an entry without text"


@pytest.mark.parametrize(("constant", "word", "first", "current"), VERSIONS)
def test_the_code_keeps_the_newest_entries_as_the_spec_reads_them(
    constant: str, word: str, first: int, current: int
) -> None:
    kept = code_entries(CODE.read_text(encoding="utf-8"), constant)
    numbers = [n for n, _ in kept]
    assert 1 <= len(kept) <= MOST_KEPT_IN_CODE, f"{constant}: {numbers}"
    assert numbers == list(range(current - len(kept) + 1, current + 1)), constant
    recorded = dict(spec_entries(SPEC.read_text(encoding="utf-8"), word))
    for n, body in kept:
        assert body == recorded.get(n), f"{constant} {n}: the code and the spec differ"


@pytest.mark.parametrize(("constant", "word", "first", "current"), VERSIONS)
def test_old_entries_keep_their_text(constant: str, word: str, first: int, current: int) -> None:
    up_to, digest = FROZEN[word]
    kept = code_entries(CODE.read_text(encoding="utf-8"), constant)
    oldest_kept = min(n for n, _ in kept)
    assert up_to == oldest_kept - 1, f"{constant}: move FROZEN up to {oldest_kept - 1}"
    entries = spec_entries(SPEC.read_text(encoding="utf-8"), word)
    assert history_digest(entries, up_to) == digest, f"{constant}: an entry up to {up_to} changed"


def _spec(*lines: str) -> str:
    return "\n".join(["# Title", "intro", *lines])


def test_entries_are_read_from_their_own_section() -> None:
    text = _spec(
        "## Verifier version",
        "purpose",
        "### Verifier 2",
        "two,",
        "  wrapped",
        "### Verifier 1",
        "one",
        "## Check version",
        "### Check 2",
        "check two",
    )
    assert spec_entries(text, "Verifier") == [(2, "two, wrapped"), (1, "one")]
    assert spec_entries(text, "Check") == [(2, "check two")]


@pytest.mark.parametrize(
    ("word", "text"),
    [
        # a check entry filed under the verifier section, and the other way round
        ("Verifier", _spec("## Verifier version", "### Check 3", "x", "## Check version")),
        ("Check", _spec("## Verifier version", "## Check version", "### Verifier 3", "x")),
        # a line starting with `#` inside an entry would end it early
        ("Verifier", _spec("## Verifier version", "### Verifier 2", "#hashtag", "x")),
        # an entry before the first section
        ("Verifier", _spec("### Verifier 2", "x", "## Verifier version", "## Check version")),
        # a section twice
        ("Check", _spec("## Verifier version", "## Check version", "## Verifier version")),
        # a section heading in another form
        ("Check", _spec("## Verifier versions", "## Check version")),
        # no section for the word
        ("Check", _spec("## Verifier version", "### Verifier 1", "x")),
    ],
)
def test_a_malformed_spec_is_an_error(word: str, text: str) -> None:
    with pytest.raises(ValueError):
        spec_entries(text, word)


def test_code_entries_are_the_comment_right_above_the_constant() -> None:
    source = "\n".join(
        [
            "#: 9 — elsewhere",
            "OTHER = 1",
            "",
            "#: what it is.",
            "#: 4 — four,",
            "#: wrapped",
            "#: 5 — five",
            'THING = "5"',
        ]
    )
    assert code_entries(source, "THING") == [(4, "four, wrapped"), (5, "five")]
