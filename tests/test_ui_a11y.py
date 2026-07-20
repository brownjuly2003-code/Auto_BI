"""A11y regression gate for the web UI (D-5, п.24).

Two defects were measured in a real browser and fixed; these tests keep them fixed.
Both checks are static (no browser, no network) so they run in the offline CI lane:

* every form control carries an accessible name — a `placeholder` is not one (it vanishes
  on input and is not a label);
* every text colour token clears WCAG AA 4.5:1 against every background token it can sit
  on — `--text-3` was #9aa1ab = 2.41:1 on `--bg` while being used by 26 selectors of
  10–15px text, including the tab labels and the pane headings.

Each check is proven against known-bad input in the same test file, so a detector that
silently stopped detecting would fail here rather than pass quietly.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "auto_bi" / "api" / "static"
INDEX = STATIC / "index.html"
CSS = STATIC / "app.css"

NAMED_CONTROLS = {"input", "select", "textarea"}


class _Controls(HTMLParser):
    """Collect form controls, label[for] targets, and elements that wrap a control."""

    def __init__(self) -> None:
        super().__init__()
        self.controls: list[dict[str, str]] = []
        self.labelled_ids: set[str] = set()
        self._label_depth: list[int] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        self._depth += 1
        if tag == "label":
            self._label_depth.append(self._depth)
            if a.get("for"):
                self.labelled_ids.add(a["for"])
        if tag in NAMED_CONTROLS:
            self.controls.append(
                {
                    "tag": tag,
                    "id": a.get("id", ""),
                    "aria_label": a.get("aria-label", ""),
                    "aria_labelledby": a.get("aria-labelledby", ""),
                    "type": a.get("type", ""),
                    "in_label": "1" if self._label_depth else "",
                }
            )
        if tag in {"input", "br", "img", "meta", "link"}:  # void elements never close
            self._depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self._label_depth:
            self._label_depth.pop()
        self._depth -= 1


def _controls(html: str) -> _Controls:
    parser = _Controls()
    parser.feed(html)
    return parser


def _unnamed(html: str) -> list[str]:
    """Controls with no accessible name — the same sources a screen reader consults."""
    parsed = _controls(html)
    missing = []
    for c in parsed.controls:
        if c["type"] in {"hidden", "submit", "button"}:
            continue  # no name needed (hidden) or named by its own value/text
        named = (
            c["aria_label"]
            or c["aria_labelledby"]
            or c["in_label"]
            or (c["id"] and c["id"] in parsed.labelled_ids)
        )
        if not named:
            missing.append(c["id"] or c["tag"])
    return missing


def test_every_form_control_has_an_accessible_name() -> None:
    assert _unnamed(INDEX.read_text(encoding="utf-8")) == []


def test_the_name_check_rejects_a_placeholder_only_control() -> None:
    # the detector must not accept a placeholder as a label, or the test above means nothing
    bad = '<form><input id="x" type="text" placeholder="Пользователь"></form>'
    assert _unnamed(bad) == ["x"]
    good = '<form><label for="x">Пользователь</label><input id="x" type="text"></form>'
    assert _unnamed(good) == []


# --- contrast ------------------------------------------------------------------------


def _relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _tokens() -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6});", CSS.read_text(encoding="utf-8")))


@pytest.mark.parametrize("text_token", ["text", "text-2", "text-3"])
@pytest.mark.parametrize("bg_token", ["bg", "surface", "surface-dim"])
def test_text_tokens_meet_wcag_aa_on_every_background(text_token: str, bg_token: str) -> None:
    tokens = _tokens()
    ratio = contrast(tokens[text_token], tokens[bg_token])
    assert ratio >= 4.5, f"--{text_token} on --{bg_token} is {ratio:.2f}:1, WCAG AA needs 4.5:1"


def test_the_contrast_formula_matches_known_values() -> None:
    # anchors from the WCAG definition: identical colours are 1:1, black on white is 21:1,
    # and the tertiary grey this gate was written for was genuinely failing
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#9aa1ab", "#f7f6f3") == pytest.approx(2.41, abs=0.01)


def test_text_tiers_stay_visually_distinct() -> None:
    # fixing contrast must not collapse the three tiers into one flat grey: each step has to
    # stay separated, or the hierarchy the layout relies on is carried by size alone
    tokens = _tokens()
    ratios = [contrast(tokens[t], tokens["bg"]) for t in ("text", "text-2", "text-3")]
    assert ratios[0] > ratios[1] > ratios[2]
    assert ratios[1] / ratios[2] >= 1.2
