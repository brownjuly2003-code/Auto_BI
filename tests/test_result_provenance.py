"""Where a candidate number came from (DECISIONOPS_ARCHITECTURE §8.4).

`attest` is pure and answers one question per finding, so the tests are one wrong field at
a time: the clean attestation is built once, and each case changes exactly the thing it is
about. What is being pinned is not "provenance is checked" but *which* wrong number the
gate can catch — the number from last month, from the other cluster, from a query nobody
reviewed — and that each of them is a question for the owner rather than a block.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from auto_bi.severity import Severity
from auto_bi.verification.provenance import (
    ResultAttestation,
    ResultOrigin,
    attest,
)
from auto_bi.verification.repository import ReferenceQuery

CREATED_AT = "2026-08-25T09:00:00Z"
CANDIDATE = "sha256:" + "c" * 64
OTHER_SNAPSHOT = "sha256:" + "0" * 64
QUERY = "sha256:" + "1" * 64
OTHER_QUERY = "sha256:" + "2" * 64
ENVIRONMENT = "clickhouse-stage:9000/dm"
SUBJECT = "asset://sales/weekly_margin_by_region@2"


def attestation(**overrides: object) -> ResultAttestation:
    return ResultAttestation.model_validate(
        {
            "origin": "query_run",
            "captured_at": CREATED_AT,
            "environment_ref": ENVIRONMENT,
            "semantic_digest": CANDIDATE,
            "query_digest": QUERY,
            "executed_by": "bi_reader",
            **overrides,
        }
    )


def grade(
    attestation: ResultAttestation | None,
    *,
    reviewed: str | None = QUERY,
    environment_ref: str = ENVIRONMENT,
    max_age: str = "PT6H",
    created_at: str = CREATED_AT,
) -> list[tuple[str, Severity]]:
    findings = attest(
        subject=SUBJECT,
        attestation=attestation,
        reviewed_query_digest=reviewed,
        candidate_digest=CANDIDATE,
        environment_ref=environment_ref,
        max_age=max_age,
        created_at=created_at,
    )
    assert all(finding.subject == SUBJECT for finding in findings)
    assert all(finding.detail.strip() for finding in findings)
    return [(finding.code, finding.severity) for finding in findings]


# --- the number this release is about -----------------------------------------


def test_a_capture_of_this_release_on_this_environment_is_established() -> None:
    assert grade(attestation()) == []


def test_a_result_with_no_attestation_is_a_question_not_a_pass() -> None:
    """The case this whole module exists for: today's silent input, now named."""
    assert grade(None) == [("provenance.unattested", Severity.REVIEW)]


def test_a_number_captured_against_other_semantics_does_not_describe_this_release() -> None:
    assert grade(attestation(semantic_digest=OTHER_SNAPSHOT)) == [
        ("provenance.other_snapshot", Severity.REVIEW)
    ]


def test_an_attestation_that_names_no_snapshot_cannot_be_tied_to_the_candidate() -> None:
    assert grade(attestation(semantic_digest=None)) == [
        ("provenance.snapshot_unstated", Severity.REVIEW)
    ]


# --- the environment the verdict is about -------------------------------------


def test_a_capture_on_another_environment_holds_another_set_of_rows() -> None:
    assert grade(attestation(environment_ref="clickhouse-prod:9000/dm")) == [
        ("provenance.other_environment", Severity.REVIEW)
    ]


def test_an_attestation_that_names_no_environment_is_asked_for_one() -> None:
    assert grade(attestation(environment_ref="")) == [
        ("provenance.environment_unstated", Severity.REVIEW)
    ]


def test_an_offline_run_compares_no_environment_at_all() -> None:
    """A run with no engine has nothing to compare against, and `dwh.*` already says so."""
    assert grade(attestation(environment_ref=""), environment_ref="") == []
    assert grade(attestation(environment_ref="anywhere:9000/dm"), environment_ref="") == []


# --- the query a reviewer signed off ------------------------------------------


def test_a_number_from_another_query_answers_a_question_nobody_reviewed() -> None:
    assert grade(attestation(query_digest=OTHER_QUERY)) == [
        ("provenance.other_query", Severity.REVIEW)
    ]


def test_an_attestation_with_no_query_is_not_tied_to_the_reviewed_one() -> None:
    assert grade(attestation(query_digest=None)) == [("provenance.query_unstated", Severity.REVIEW)]


def test_an_asset_with_no_reviewed_query_leaves_the_digest_proving_nothing() -> None:
    """Advisory, not review: the gap is in the asset, and it is the owner's to close."""
    assert grade(attestation(), reviewed=None) == [
        ("provenance.query_unreviewed", Severity.ADVISORY)
    ]


# --- how the number was obtained ----------------------------------------------


def test_a_hand_entered_number_is_never_provenance() -> None:
    assert grade(attestation(origin="manual", query_digest=None)) == [
        ("provenance.hand_entered", Severity.REVIEW)
    ]


def test_a_surface_export_shows_what_the_surface_showed() -> None:
    assert grade(attestation(origin="bi_export", query_digest=None)) == [
        ("provenance.surface_export", Severity.REVIEW)
    ]


def test_an_origin_that_ran_no_query_is_not_also_asked_for_a_query_digest() -> None:
    """One fact, one finding: a BI export has no query, and saying so twice weighs twice."""
    codes = grade(attestation(origin="bi_export", query_digest=None))
    assert [code for code, _ in codes] == ["provenance.surface_export"]


# --- when the number was read -------------------------------------------------


def test_a_reading_older_than_the_declared_window_is_an_old_reading() -> None:
    assert grade(attestation(captured_at="2026-08-24T09:00:00Z")) == [
        ("provenance.stale_capture", Severity.REVIEW)
    ]


def test_a_reading_inside_the_declared_window_is_current_enough() -> None:
    assert grade(attestation(captured_at="2026-08-25T04:00:00Z")) == []


def test_a_reading_from_after_the_run_means_the_two_do_not_belong_together() -> None:
    assert grade(attestation(captured_at="2026-08-25T10:00:00Z")) == [
        ("provenance.capture_after_run", Severity.REVIEW)
    ]


def test_a_window_with_no_fixed_length_is_not_graded_here() -> None:
    """`dwh.freshness` already tells the owner `P1M` has no seconds; twice would weigh twice."""
    assert grade(attestation(captured_at="2026-01-01T09:00:00Z"), max_age="P1M") == []


# --- more than one thing wrong ------------------------------------------------


def test_every_wrong_field_is_named_rather_than_the_first_one() -> None:
    """An owner fixing a capture pipeline needs the whole list, not one round trip each."""
    codes = grade(
        attestation(
            origin="manual",
            environment_ref="clickhouse-prod:9000/dm",
            semantic_digest=OTHER_SNAPSHOT,
            captured_at="2026-08-01T09:00:00Z",
        )
    )
    assert [code for code, _ in codes] == [
        "provenance.hand_entered",
        "provenance.other_snapshot",
        "provenance.other_environment",
        "provenance.stale_capture",
    ]


def test_no_provenance_finding_ever_blocks_a_release() -> None:
    """Grading is `results.reconcile`'s job when numbers disagree; this one asks a human."""
    for attested in (
        None,
        attestation(origin="manual"),
        attestation(origin="bi_export"),
        attestation(semantic_digest=OTHER_SNAPSHOT),
        attestation(semantic_digest=None),
        attestation(environment_ref="elsewhere:9000/dm"),
        attestation(environment_ref=""),
        attestation(query_digest=OTHER_QUERY),
        attestation(query_digest=None),
        attestation(captured_at="2026-01-01T09:00:00Z"),
        attestation(captured_at="2026-12-01T09:00:00Z"),
    ):
        assert all(severity is not Severity.BLOCK for _, severity in grade(attested))


# --- the attestation contract itself ------------------------------------------


def test_an_attestation_must_carry_a_real_content_address() -> None:
    with pytest.raises(ValidationError):
        attestation(query_digest="deadbeef")


def test_an_attestation_must_carry_a_utc_timestamp() -> None:
    with pytest.raises(ValidationError):
        attestation(captured_at="2026-08-25 09:00:00")


def test_an_unknown_attestation_field_is_a_typo_not_a_default() -> None:
    with pytest.raises(ValidationError):
        attestation(captured_by="bi_reader")


def test_an_unknown_origin_is_rejected_rather_than_read_as_a_query_run() -> None:
    with pytest.raises(ValidationError):
        attestation(origin="dbt_test")


def test_every_origin_the_enum_declares_is_graded() -> None:
    """A new origin must not fall through the grader and read as established."""
    for origin in ResultOrigin:
        codes = grade(attestation(origin=origin.value))
        assert (codes == []) is (origin is ResultOrigin.QUERY_RUN)


# --- the reviewed query the attestation points at ------------------------------


def test_a_reviewed_query_is_addressed_by_its_engine_and_its_text() -> None:
    """One statement means two things on two engines, so both are in the address."""
    sql = 'SELECT "region" FROM "dm"."sales_daily"'
    assert (
        ReferenceQuery(engine="clickhouse", sql=sql).digest()
        != ReferenceQuery(engine="greenplum", sql=sql).digest()
    )
    assert (
        ReferenceQuery(engine="clickhouse", sql=sql).digest()
        == ReferenceQuery(engine="clickhouse", sql=sql).digest()
    )


def test_a_reviewed_query_that_is_not_a_select_is_a_broken_reviewed_artifact() -> None:
    """Invariant 7 holds at the contract boundary, not at the moment of execution."""
    with pytest.raises(ValidationError, match="not a plain SELECT"):
        ReferenceQuery(engine="clickhouse", sql="INSERT INTO dm.sales_daily VALUES (1)")


def test_a_reviewed_query_may_not_read_a_remote_source() -> None:
    with pytest.raises(ValidationError, match="not a plain SELECT"):
        ReferenceQuery(engine="clickhouse", sql="SELECT * FROM url('http://x', CSV)")


def test_a_reviewed_query_names_an_engine_the_seam_knows() -> None:
    """An unknown name would be guarded with a substituted grammar, which proves nothing."""
    with pytest.raises(ValidationError, match="unknown engine"):
        ReferenceQuery(engine="duckdb", sql="SELECT 1")


def test_a_postgres_reviewed_query_is_guarded_with_the_postgres_grammar() -> None:
    query = ReferenceQuery(
        engine="greenplum",
        sql='SELECT "region", sum("revenue") AS "r" FROM "dm"."sales_daily" GROUP BY "region"',
    )
    assert query.digest().startswith("sha256:")
