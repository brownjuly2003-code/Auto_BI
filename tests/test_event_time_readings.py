"""The newest event time a DWH reports is read in one of three forms.

Spec: docs/specs/event-time-readings.md. The values handed to the probe are the ones the locked
drivers give back for `max(<column>)`: clickhouse-connect a naive `datetime` for a `DateTime` it
reads in UTC and an aware one for any other zone, psycopg a naive one for `timestamp` and an aware
one, in the session's zone, for `timestamptz`; both a `date` for a date. A day and a clock with
no zone are read in the timezone the metric declares; the instants below are the ones the zone
database the tests run on gives for it.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from auto_bi.cli import main
from auto_bi.evidence.canonical import digest_bytes
from auto_bi.evidence.manifest import MANIFEST_PATH, EvidenceConflictError, verify_bundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.semantic.gateway import NATIVE_SOURCE_VERSION
from auto_bi.severity import Severity
from auto_bi.verification import environment
from auto_bi.verification.environment import (
    EnvironmentFacts,
    EnvironmentQuestions,
    _read,
    _Reading,
    check_freshness,
)
from auto_bi.verification.environment_probe import collect
from auto_bi.verification.release_gate import CHECK_VERSION, VERIFIER_VERSION, run_gate
from tests.test_decision_verify import Case, probing, summary_of
from tests.test_decision_verify import environment as gate_environment
from tests.test_environment_checks import CREATED_AT, fact, facts
from tests.test_environment_probe import clickhouse_engine, postgres_engine, questions
from tests.test_run_identity import ROWS, _inputs, _store
from tests.test_utc_timestamps import ACCEPTED, REFUSED, _ReadsAnything, _ReadsNothing

MOSCOW = timezone(timedelta(hours=3))
NEW_YORK = timezone(timedelta(hours=-4))
SAO_PAULO = timezone(timedelta(hours=-3))
FORMS = (
    "which is none of the forms this check reads: a day YYYY-MM-DD, a clock YYYY-MM-DDTHH:MM:SS "
    "with no zone, or a UTC moment such as 2026-08-25T09:00:00Z"
)
REPORTS = "dm.sales_daily on 127.0.0.1:8124/dm reports"
NOT_GRADED = "so the instant it names is not known and its age is not graded"
AHEAD = "a source cannot be dated after the run that reads it, so its age is not graded"
PILOT = Path(__file__).resolve().parents[1] / "docs" / "pilot" / "2026-08-25-ch-control"


def sealed(engine: str, value: object) -> EnvironmentFacts:
    """What the probe seals when the engine's driver gives back *value* as the newest time."""
    row = [{"rows_seen": 600, "max_event_time": value}]
    if engine == "clickhouse":
        return collect(
            clickhouse_engine(**{"count()": row}), questions(), engine=engine, target="s"
        )
    return collect(postgres_engine(**{"count(*)": row}), questions(), engine=engine, target="s")


def graded(
    event_time: str,
    created_at: str = CREATED_AT,
    *,
    zones: list[str] | None = None,
    max_age: str = "PT6H",
) -> list[tuple[str, Severity, str]]:
    """The check's findings for one reading; the metric declares Europe/Moscow unless *zones*."""
    asked = questions()
    if zones is not None:
        asked.tables[0].timezones = zones
    found = check_freshness(
        asked, facts(fact(max_event_time=event_time)), max_age=max_age, created_at=created_at
    )
    return [(item.code, item.severity, item.detail) for item in found]


def unreadable(event_time: str) -> list[tuple[str, Severity, str]]:
    detail = f"{REPORTS} {event_time!r} as its newest date, {FORMS}"
    return [("freshness.unreadable_event_time", Severity.REVIEW, detail)]


def stale(
    event_time: str, age: str, read_as: str, created_at: str = CREATED_AT
) -> list[tuple[str, Severity, str]]:
    detail = (
        f"dm.sales_daily is dated {event_time} by date ({age} before {created_at}{read_as}), "
        "older than the PT6H this product declares"
    )
    return [("freshness.stale", Severity.REVIEW, detail)]


# --- the probe spells an instant once, in UTC -------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "value"),
    [
        pytest.param("clickhouse", datetime(2026, 8, 25, 6, 30), id="clickhouse naive"),
        pytest.param("clickhouse", datetime(2026, 8, 25, 6, 30, tzinfo=UTC), id="clickhouse UTC"),
        pytest.param(
            "clickhouse", datetime(2026, 8, 25, 9, 30, tzinfo=MOSCOW), id="clickhouse +03"
        ),
        pytest.param(
            "clickhouse", datetime(2026, 8, 25, 2, 30, tzinfo=NEW_YORK), id="clickhouse -04"
        ),
        pytest.param("greengage", datetime(2026, 8, 25, 6, 30, tzinfo=UTC), id="psycopg UTC"),
        pytest.param("greengage", datetime(2026, 8, 25, 9, 30, tzinfo=MOSCOW), id="psycopg +03"),
    ],
)
def test_one_instant_is_sealed_once_whatever_zone_the_driver_read_it_in(
    engine: str, value: datetime
) -> None:
    assert sealed(engine, value).tables[0].max_event_time == "2026-08-25T06:30:00Z"


@pytest.mark.parametrize("zone", [None, UTC], ids=["naive", "UTC"])
def test_the_microseconds_of_a_clickhouse_instant_are_six_digits_before_the_z(
    zone: timezone | None,
) -> None:
    value = datetime(2026, 8, 25, 6, 30, 0, 120000, tzinfo=zone)

    assert sealed("clickhouse", value).tables[0].max_event_time == "2026-08-25T06:30:00.120000Z"


@pytest.mark.parametrize(
    ("engine", "value", "text"),
    [
        pytest.param("greengage", datetime(2026, 8, 25, 4), "2026-08-25T04:00:00", id="timestamp"),
        pytest.param(
            "greengage",
            datetime(2026, 8, 25, 4, 0, 0, 500000),
            "2026-08-25T04:00:00.500000",
            id="timestamp with microseconds",
        ),
        pytest.param("greengage", date(2026, 8, 24), "2026-08-24", id="date"),
        pytest.param("clickhouse", date(2026, 8, 24), "2026-08-24", id="Date"),
    ],
)
def test_a_clock_with_no_zone_and_a_day_keep_their_form(
    engine: str, value: object, text: str
) -> None:
    """psycopg gives back a `timestamp`, which has no zone, as a naive `datetime`."""
    assert sealed(engine, value).tables[0].max_event_time == text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        pytest.param(
            datetime(9999, 12, 31, 23, tzinfo=SAO_PAULO),
            "9999-12-31T23:00:00-03:00",
            id="past 9999",
        ),
        pytest.param(
            datetime(1, 1, 1, 1, tzinfo=MOSCOW), "0001-01-01T01:00:00+03:00", id="before 0001"
        ),
    ],
)
def test_an_instant_utc_cannot_spell_keeps_its_offset_and_is_unreadable(
    value: datetime, text: str
) -> None:
    reading = sealed("greengage", value).tables[0].max_event_time

    assert reading == text
    assert graded(text) == unreadable(text)


@pytest.mark.parametrize(
    ("value", "text"),
    [(" 2026-08-25 04:00:00 ", "2026-08-25 04:00:00"), (20260824, "20260824")],
    ids=["text", "a number"],
)
def test_text_is_the_engines_own_and_only_stripped(value: object, text: str) -> None:
    reading = sealed("greengage", value).tables[0].max_event_time

    assert reading == text
    assert graded(text) == unreadable(text)


def test_text_in_a_form_the_check_reads_is_read_in_that_form() -> None:
    """Known boundary: the check reads the sealed text, not the column's type, so a text column
    that holds a clock is read as that clock."""
    reading = sealed("greengage", " 2026-08-25T04:00:00 ").tables[0].max_event_time

    assert reading == "2026-08-25T04:00:00"
    assert graded(reading) == graded("2026-08-25T04:00:00")
    assert [code for code, _, _ in graded(reading)] == ["freshness.stale"]


# --- the check reads three forms, by the form and the calendar --------------------------------


@pytest.mark.parametrize(
    ("event_time", "clock", "form"),
    [
        ("2026-08-24", datetime(2026, 8, 24), "day"),
        ("2024-02-29", datetime(2024, 2, 29), "day"),
        ("2026-08-25T04:00:00", datetime(2026, 8, 25, 4), "clock"),
        ("2026-08-25T04:00:00.5", datetime(2026, 8, 25, 4, 0, 0, 500000), "clock"),
        ("2026-08-25T06:30:00Z", datetime(2026, 8, 25, 6, 30), "moment"),
        ("2026-08-25T06:30:00.123456789Z", datetime(2026, 8, 25, 6, 30, 0, 123456), "moment"),
    ],
)
def test_the_three_forms_are_read(event_time: str, clock: datetime, form: str) -> None:
    assert _read(event_time) == _Reading(clock, form)


NOT_READ = [
    pytest.param("2026-W35-2", id="a week and a day"),
    pytest.param("20260824", id="the basic date"),
    pytest.param("2026-08-25 04:00:00", id="a blank for the T"),
    pytest.param("2026-08-25T09:30:00+03:00", id="an offset"),
    pytest.param("2026-08-25T06:30:00.123456abcZ", id="letters after the sixth digit"),
    pytest.param("2026-08-25T06:30Z", id="no seconds"),
    pytest.param("2026-08-25T24:00:00", id="the hour 24"),
    pytest.param("2025-02-29", id="no leap day"),
    pytest.param(chr(0x662) + chr(0x660) + chr(0x662) + chr(0x666) + "-08-24", id="Arabic-Indic"),
    pytest.param(" 2026-08-24", id="a blank ahead"),
    pytest.param("2026-08-24\n", id="a line break behind"),
    pytest.param("not a time", id="a phrase"),
]


@pytest.mark.parametrize("event_time", NOT_READ)
def test_what_fromisoformat_read_the_check_names_unreadable(event_time: str) -> None:
    assert graded(event_time) == unreadable(event_time)


EXPECTED = {
    "2026-08-25T09:00:00Z": datetime(2026, 8, 25, 9),
    "2026-08-25T09:00:00.5Z": datetime(2026, 8, 25, 9, 0, 0, 500000),
    "2026-08-25T09:00:00.500Z": datetime(2026, 8, 25, 9, 0, 0, 500000),
    "2026-08-25T09:00:00.0Z": datetime(2026, 8, 25, 9),
    "2026-08-25T09:00:00.123456Z": datetime(2026, 8, 25, 9, 0, 0, 123456),
    "2026-08-25T09:00:00.123456789Z": datetime(2026, 8, 25, 9, 0, 0, 123456),
    "2026-08-25T09:00:00." + "7" * 400 + "Z": datetime(2026, 8, 25, 9, 0, 0, 777777),
    "2024-02-29T23:59:59Z": datetime(2024, 2, 29, 23, 59, 59),
    "2000-02-29T00:00:00Z": datetime(2000, 2, 29),
    "0001-01-01T00:00:00Z": datetime(1, 1, 1),
    "9999-12-31T23:59:59.999999999Z": datetime(9999, 12, 31, 23, 59, 59, 999999),
}


@pytest.mark.parametrize("moment", ACCEPTED)
def test_every_moment_a_timestamp_takes_is_read_as_the_instant_it_names(moment: str) -> None:
    assert _read(moment) == _Reading(EXPECTED[moment], "moment")


@pytest.mark.parametrize("text", [item for item in REFUSED if item.values != ("2026-08-25",)])
def test_no_text_a_timestamp_refuses_is_read_but_the_date_alone(text: str) -> None:
    assert _read(text) is None


@pytest.mark.parametrize("reader", [_ReadsAnything, _ReadsNothing], ids=["anything", "nothing"])
def test_the_answer_is_not_what_fromisoformat_reads(
    monkeypatch: pytest.MonkeyPatch, reader: type[datetime]
) -> None:
    """Python 3.14 reads `24:00` and 3.13 does not; the check asks neither of them."""
    monkeypatch.setattr(environment, "datetime", reader)

    for text in ("2026-08-25T24:00:00", "2026-08-25T24:00:00Z", "2026-W35-2"):
        assert graded(text) == unreadable(text)
    assert graded("2026-08-25T06:30:00Z") == []
    assert [code for code, _, _ in graded("2026-08-25T01:00:00Z")] == ["freshness.stale"]


@pytest.mark.parametrize(
    "created_at", ["2026-08-25", "2026-08-25T09:00:00", "2026-08-25T12:00:00+03:00"]
)
def test_the_runs_moment_is_a_timestamp(created_at: str) -> None:
    refusal = f"created_at must be a Timestamp, not {created_at!r}"

    for event_time in ("2026-08-24", "2026-08-25T06:30:00Z"):
        with pytest.raises(ValueError, match=f"^{re.escape(refusal)}$"):
            graded(event_time, created_at=created_at)


# --- a day or a clock with no zone is read in the one zone the metrics declare ----------------


def test_a_clock_with_no_zone_is_read_in_the_zone_the_metric_declares() -> None:
    """Read as UTC, `10:00:00` would be an hour after the run; in Moscow it is two hours old."""
    assert graded("2026-08-25T04:00:00") == stale(
        "2026-08-25T04:00:00", "8.0h", ", read in Europe/Moscow as 2026-08-25T01:00:00Z"
    )
    assert graded("2026-08-25T10:00:00") == []


def test_a_day_is_read_as_its_00_00_in_the_zone_the_metric_declares() -> None:
    """`2026-08-25` is twelve hours old in Moscow, against nine as a UTC day."""
    assert graded("2026-08-24") == stale(
        "2026-08-24", "36.0h", ", read as 00:00 of that day in Europe/Moscow, 2026-08-23T21:00:00Z"
    )
    assert [code for code, _, _ in graded("2026-08-25", max_age="PT10H")] == ["freshness.stale"]
    assert graded("2026-08-25", max_age="PT12H") == []


def test_a_utc_moment_is_read_without_a_zone() -> None:
    """A moment names its instant, so no declared zone, several, or an unknown one is moot."""
    expected = stale("2026-08-25T01:00:00Z", "8.0h", "")

    for zones in ([], ["Europe/Moscow", "UTC"], ["Mars/Olympus"], ["Europe/Moscow"]):
        assert graded("2026-08-25T01:00:00Z", zones=zones) == expected


def test_a_zone_two_metrics_declare_is_one_zone() -> None:
    assert graded("2026-08-24", zones=["Europe/Moscow", "Europe/Moscow"]) == graded("2026-08-24")


BACKSLASHED = "Europe" + chr(92) + "Moscow"
NOT_HELD = "which the zone database this gate reads does not hold"
ZONE_UNKNOWN = [
    pytest.param([], "the metrics that read it declare no timezone", id="none"),
    pytest.param(
        ["Europe/Moscow", "UTC"],
        "the metrics that read it declare 2 timezones, 'Europe/Moscow', 'UTC'",
        id="two",
    ),
    pytest.param(
        ["Europe/Moscow", "W-SU"],
        "the metrics that read it declare 2 timezones, 'Europe/Moscow', 'W-SU'",
        id="two names of one zone",
    ),
    pytest.param(
        ["Mars/Olympus"],
        f"the metrics that read it declare 'Mars/Olympus', {NOT_HELD}",
        id="no such zone",
    ),
    pytest.param(["utc"], f"the metrics that read it declare 'utc', {NOT_HELD}", id="utc"),
    pytest.param(
        ["europe/moscow"],
        f"the metrics that read it declare 'europe/moscow', {NOT_HELD}",
        id="small letters",
    ),
    pytest.param(
        ["Europe/Moscow "],
        f"the metrics that read it declare 'Europe/Moscow ', {NOT_HELD}",
        id="a blank behind",
    ),
    pytest.param(
        [BACKSLASHED],
        f"the metrics that read it declare {BACKSLASHED!r}, {NOT_HELD}",
        id="a backslash",
    ),
]


@pytest.mark.parametrize(("zones", "why"), ZONE_UNKNOWN)
@pytest.mark.parametrize(
    ("event_time", "form"), [("2026-08-24", "day"), ("2026-08-25T04:00:00", "clock")]
)
def test_a_reading_with_no_one_zone_to_read_it_in_is_review(
    zones: list[str], why: str, event_time: str, form: str
) -> None:
    """No silent UTC. `ZoneInfo` on Windows opens `utc`, `Europe/Moscow ` and the backslash;
    the zone database does not list them."""
    detail = (
        f"{REPORTS} {event_time!r} as its newest date, a {form} with no zone; {why}, {NOT_GRADED}"
    )

    assert graded(event_time, zones=zones) == [
        ("freshness.event_time_zone_unknown", Severity.REVIEW, detail)
    ]


@pytest.mark.parametrize("zone", ["UTC", "Europe/Moscow"])
@pytest.mark.parametrize(
    ("event_time", "form"), [("2026-08-24", "day"), ("2026-08-25T04:00:00", "clock")]
)
def test_a_host_with_no_zone_database_reads_no_day_and_no_clock(
    monkeypatch: pytest.MonkeyPatch, zone: str, event_time: str, form: str
) -> None:
    """A gate host whose zone database lists no name, with no system database and no `tzdata`
    package, reads no day and no clock, a declared `UTC` included; a UTC moment it reads."""
    monkeypatch.setattr(environment, "available_timezones", frozenset)
    detail = (
        f"{REPORTS} {event_time!r} as its newest date, a {form} with no zone; the metrics that "
        f"read it declare {zone!r}, {NOT_HELD}, {NOT_GRADED}"
    )

    assert graded(event_time, zones=[zone]) == [
        ("freshness.event_time_zone_unknown", Severity.REVIEW, detail)
    ]
    assert graded("2026-08-25T01:00:00Z", zones=[zone]) == stale("2026-08-25T01:00:00Z", "8.0h", "")


# --- a clock the zone skips or repeats --------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "event_time", "created_at", "age", "read_as"),
    [
        pytest.param(
            "Europe/Berlin",
            "2026-03-29T02:30:00",
            "2026-03-29T12:00:00Z",
            "10.5h",
            ", read in Europe/Berlin as 2026-03-29T01:30:00Z",
            id="a clock the zone skips: the offset before the skip",
        ),
        pytest.param(
            "Europe/Berlin",
            "2026-10-25T02:30:00",
            "2026-10-25T12:00:00Z",
            "11.5h",
            ", read in Europe/Berlin as 2026-10-25T00:30:00Z",
            id="a clock the zone repeats: its first instant",
        ),
        pytest.param(
            "America/Havana",
            "2026-03-08",
            "2026-03-08T12:00:00Z",
            "7.0h",
            ", read as 00:00 of that day in America/Havana, 2026-03-08T05:00:00Z",
            id="a day whose 00:00 the zone skips",
        ),
        pytest.param(
            "America/Havana",
            "2026-11-01",
            "2026-11-01T12:00:00Z",
            "8.0h",
            ", read as 00:00 of that day in America/Havana, 2026-11-01T04:00:00Z",
            id="a day whose 00:00 the zone repeats",
        ),
    ],
)
def test_a_clock_the_zone_skips_or_repeats_takes_the_offset_before_its_clocks_moved(
    zone: str, event_time: str, created_at: str, age: str, read_as: str
) -> None:
    assert graded(event_time, created_at, zones=[zone]) == stale(
        event_time, age, read_as, created_at
    )


@pytest.mark.parametrize(
    ("zone", "day", "first"),
    [
        ("Europe/Moscow", date(2026, 8, 24), datetime(2026, 8, 23, 21, tzinfo=UTC)),
        ("America/Havana", date(2026, 3, 8), datetime(2026, 3, 8, 5, tzinfo=UTC)),
        ("America/Havana", date(2026, 11, 1), datetime(2026, 11, 1, 4, tzinfo=UTC)),
        ("Pacific/Kiritimati", date(2026, 8, 26), datetime(2026, 8, 25, 10, tzinfo=UTC)),
    ],
)
def test_the_00_00_a_day_is_read_as_is_the_first_instant_of_that_day(
    zone: str, day: date, first: datetime
) -> None:
    there = ZoneInfo(zone)

    assert first.astimezone(there).date() == day
    assert (first - timedelta(microseconds=1)).astimezone(there).date() < day


def test_a_day_whose_00_00_a_skip_spans_is_read_after_it_began() -> None:
    """Known boundary: Toronto moved its clocks from 23:30 to 00:30 on 1919-03-30, so 31 March
    began at 04:30Z, and its 00:00, read with the offset before the skip, is 05:00Z."""
    toronto = ZoneInfo("America/Toronto")
    began = datetime(1919, 3, 31, 4, 30, tzinfo=UTC)

    assert began.astimezone(toronto).isoformat() == "1919-03-31T00:30:00-04:00"
    assert (began - timedelta(microseconds=1)).astimezone(toronto).date() == date(1919, 3, 30)
    assert graded("1919-03-31", "1919-03-31T12:00:00Z", zones=["America/Toronto"]) == stale(
        "1919-03-31",
        "7.0h",
        ", read as 00:00 of that day in America/Toronto, 1919-03-31T05:00:00Z",
        "1919-03-31T12:00:00Z",
    )


# --- a reading after the run ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_time", "zones", "read_as"),
    [
        pytest.param("2026-08-25T10:00:00Z", None, "", id="a moment"),
        pytest.param("2026-08-25T09:00:00.000001Z", [], "", id="a microsecond after"),
        pytest.param(
            "2026-08-25T13:00:00",
            None,
            ", read in Europe/Moscow as 2026-08-25T10:00:00Z",
            id="a clock",
        ),
        pytest.param(
            "2026-08-26",
            ["Pacific/Kiritimati"],
            ", read as 00:00 of that day in Pacific/Kiritimati, 2026-08-25T10:00:00Z",
            id="a day of UTC+14",
        ),
        pytest.param(
            "9999-12-31",
            ["America/Sao_Paulo"],
            ", read as 00:00 of that day in America/Sao_Paulo, 9999-12-31T03:00:00Z",
            id="the last day",
        ),
    ],
)
def test_a_reading_after_the_run_is_review_and_its_age_is_not_graded(
    event_time: str, zones: list[str] | None, read_as: str
) -> None:
    detail = (
        f"{REPORTS} {event_time!r} as its newest date{read_as}, after the run's created_at "
        f"{CREATED_AT}: {AHEAD}"
    )

    assert graded(event_time, zones=zones) == [
        ("freshness.event_time_ahead", Severity.REVIEW, detail)
    ]


@pytest.mark.parametrize(
    "event_time", ["2026-08-25T09:00:00Z", "2026-08-25T09:00:00.0000009Z", "2026-08-25T12:00:00"]
)
def test_a_reading_at_the_runs_own_instant_is_not_after_it(event_time: str) -> None:
    """No tolerance either way; digits of a fraction after the sixth are not counted."""
    assert graded(event_time) == []


# --- an instant UTC cannot spell --------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_time", "zone", "form"),
    [
        ("0001-01-01", "Europe/Moscow", "day"),
        ("0001-01-01T01:00:00", "Europe/Moscow", "clock"),
        ("9999-12-31T23:00:00", "America/Sao_Paulo", "clock"),
    ],
)
def test_a_reading_that_falls_outside_the_years_utc_spells_is_unreadable(
    event_time: str, zone: str, form: str
) -> None:
    detail = (
        f"{REPORTS} {event_time!r} as its newest date, a {form} that read in {zone} falls "
        "outside the years 0001 to 9999 of UTC, where no instant is read"
    )

    assert graded(event_time, zones=[zone]) == [
        ("freshness.unreadable_event_time", Severity.REVIEW, detail)
    ]


# --- what is asked first ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_time", ["2026-08-24", "2026-08-25T10:00:00Z", "0001-01-01", "not a time"]
)
def test_a_window_in_months_is_named_before_the_reading_is_read(event_time: str) -> None:
    """Known boundary: with no fixed window the age is not compared, and the reading's own
    form, zone and instant are not graded either."""
    assert [code for code, _, _ in graded(event_time, zones=[], max_age="P1M")] == [
        "freshness.window_not_comparable"
    ]


# --- one instant is one bundle ----------------------------------------------------------------


def test_one_instant_through_three_driver_values_is_one_bundle(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path / "case")
    store = _store(tmp_path)
    values = (
        datetime(2026, 8, 25, 6, 30, tzinfo=UTC),
        datetime(2026, 8, 25, 9, 30, tzinfo=MOSCOW),
        datetime(2026, 8, 25, 6, 30),
    )

    reports = []
    for value in values:
        reading = sealed("clickhouse", value).tables[0].max_event_time
        run = dataclasses.replace(inputs, environment=gate_environment(max_event_time=reading))
        reports.append(run_gate(run, store=store, created_at=CREATED_AT))

    assert len({(report.run_id, report.evidence_digest) for report in reports}) == 1


def test_two_hosts_whose_zone_databases_differ_seal_two_bundles_under_one_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The zone database is not sealed. A host that lists Europe/Moscow and one that lists no
    zone grade one clock apart under one run id; one store keeps the bundle it took first."""
    clock = gate_environment(max_event_time="2026-08-25T04:00:00")
    inputs = dataclasses.replace(_inputs(tmp_path / "case"), environment=clock)
    store = _store(tmp_path)
    first = run_gate(inputs, store=store, created_at=CREATED_AT)
    monkeypatch.setattr(environment, "available_timezones", frozenset)
    other = run_gate(inputs, store=_store(tmp_path / "other"), created_at=CREATED_AT)

    assert other.run_id == first.run_id
    assert other.evidence_digest != first.evidence_digest
    assert summary_of(first, "dwh.freshness") == "freshness.stale"
    assert summary_of(other, "dwh.freshness") == "freshness.event_time_zone_unknown"
    with pytest.raises(EvidenceConflictError, match="same idempotency key, different evidence"):
        run_gate(inputs, store=store, created_at=CREATED_AT)
    assert digest_bytes(store.get(first.run_id, MANIFEST_PATH)) == first.evidence_digest
    assert verify_bundle(store, first.run_id) == []


# --- bundles sealed before ----------------------------------------------------------------------


def test_a_bundle_whose_facts_hold_an_offset_reads_as_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`decision evidence` checks an artifact by its digest and size; no moment in it is read."""
    case = Case(tmp_path).with_asset().with_results(ROWS)
    facts_then = gate_environment(max_event_time="2026-08-25T09:30:00+03:00")
    report = case.run(probe=probing(facts_then))
    run = case.root / "evidence" / report.run_id

    assert main(["decision", "evidence", str(run), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["problems"] == []


def test_a_bundle_that_read_a_day_in_utc_keeps_its_detail_and_still_verifies() -> None:
    """A pilot bundle of an older verifier read `2026-08-24` as its start in UTC. Verifying it
    grades nothing again; the check of today reads the same facts in Europe/Moscow."""
    evidence = PILOT / "evidence"
    (run,) = (path.name for path in evidence.iterdir())
    check = json.loads(
        (evidence / run / "checks" / "check-dwh.freshness.json").read_text(encoding="utf-8")
    )
    today = check_freshness(
        EnvironmentQuestions.model_validate(check["questions"]),
        EnvironmentFacts.model_validate(check["facts"]),
        max_age="PT6H",
        created_at="2026-08-25T21:00:00Z",
    )

    assert verify_bundle(FilesystemArtifactStore(evidence), run) == []
    assert [item["detail"] for item in check["findings"]] == [
        "dm.sales_daily is dated 2026-08-24 (45.0h before 2026-08-25T21:00:00Z, read as "
        "2026-08-24T00:00:00Z while the metric declares timezone Europe/Moscow), older than the "
        "PT6H this product declares"
    ]
    assert [item.detail for item in today] == [
        "dm.sales_daily is dated 2026-08-24 by date (2.0d before 2026-08-25T21:00:00Z, read as "
        "00:00 of that day in Europe/Moscow, 2026-08-23T21:00:00Z), older than the PT6H this "
        "product declares"
    ]


# --- versions ---------------------------------------------------------------------------------


def test_the_gate_that_reads_them_so_is_a_new_verifier_with_a_new_check_rule() -> None:
    """The probe seals another text and the rule grades a day and a clock anew (§7.6)."""
    assert int(VERIFIER_VERSION) >= 120
    assert int(CHECK_VERSION) >= 80
    assert NATIVE_SOURCE_VERSION == "3"
