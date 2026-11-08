"""Boundary rules a crafted input must not cross (adversarial review of the kernel).

Each test here is a hole that was open in the first cut of the contract kernel and is now
closed. They share one theme: a value that *looks* identical to a valid one in any report
must not be accepted, and a value that is accepted must stay representable as a digest.

* Python's ``$`` matches before a trailing newline, so ``"metric://sales/x\\n"`` used to be
  a valid id — indistinguishable on screen, different in every hash.
* A bundle is assembled, not merely parsed, so `digest()` re-validates instead of trusting
  the state it is handed.
* A path is confined by a whitelist, because a prefix check still admits `name:stream`,
  trailing dots and device names — all of which mean something else to the filesystem.
* `main` / `HEAD` / `latest` are not pins.
* A contract that parses must be hashable: integers stay inside the safe range, decimals are
  normalized, and `budgets: {}` is not a second spelling of "no budgets".
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from auto_bi.decision.spec import DecisionProductSpec
from auto_bi.evidence.model import ClaimType, EvidenceBundle, EvidenceIntegrity
from auto_bi.refs import RefError, parse_ref
from auto_bi.semantic.metric_contract import ExternalFormula, MetricContract
from tests.test_evidence_bundle import artifact, bundle, claim

FIXTURES = Path(__file__).parent / "fixtures" / "decision"


def product(**overrides: Any) -> dict[str, Any]:
    data = deepcopy(yaml.safe_load((FIXTURES / "weekly_margin.yaml").read_text(encoding="utf-8")))
    data.update(overrides)
    return data


def metric(**overrides: Any) -> dict[str, Any]:
    data = deepcopy(
        yaml.safe_load((FIXTURES / "gross_margin_pct.yaml").read_text(encoding="utf-8"))
    )
    data.update(overrides)
    return data


# --- trailing newline ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "metric://sales/gross_margin_pct@3\n",
        "metric://sales\n/gross_margin_pct@3",
        "metric://sales/gross_margin_pct\n",
    ],
)
def test_ref_with_a_trailing_newline_is_rejected(raw: str) -> None:
    with pytest.raises(RefError):
        parse_ref(raw)


def test_contract_id_with_a_newline_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MetricContract.model_validate(
            metric(metadata={**metric()["metadata"], "id": "metric://sales/gross_margin_pct\n"})
        )


def test_decimal_duration_digest_and_slug_reject_a_trailing_newline() -> None:
    with pytest.raises(ValidationError, match="decimal"):
        DecisionProductSpec.model_validate(
            product(
                verification={**product()["verification"], "cross_surface_tolerance": "0.000001\n"}
            )
        )
    with pytest.raises(ValidationError, match="duration"):
        DecisionProductSpec.model_validate(
            product(scope={**product()["scope"], "time_window": "P13W\n"})
        )
    with pytest.raises(ValidationError, match="sha256"):
        EvidenceBundle.model_validate(
            bundle(artifacts=[artifact(digest=artifact()["digest"] + "\n")])
        )
    with pytest.raises(ValidationError, match="slug"):
        EvidenceBundle.model_validate(bundle(run_id="run-2026-08-24-01\n"))


# --- decimal normalization -----------------------------------------------------------


def test_decimal_strings_are_normalized_to_one_spelling() -> None:
    padded = DecisionProductSpec.model_validate(
        product(verification={**product()["verification"], "cross_surface_tolerance": "0.0000010"})
    )
    plain = DecisionProductSpec.load(FIXTURES / "weekly_margin.yaml")
    assert padded.verification.cross_surface_tolerance == "0.000001"
    assert padded.digest() == plain.digest()


def test_claim_value_is_normalized_too() -> None:
    parsed = EvidenceBundle.model_validate(bundle(claims=[claim(value="-0.00310")]))
    assert parsed.claims[0].value == "-0.0031"


# --- assembled state is re-validated before it is hashed -----------------------------


def test_digest_refuses_a_bundle_mutated_after_parsing() -> None:
    parsed = EvidenceBundle.model_validate(bundle())
    parsed.claims[0].claim_type = ClaimType.CAUSAL
    with pytest.raises(ValidationError, match="causal"):
        parsed.digest()


def test_digest_refuses_a_path_escaped_after_parsing() -> None:
    parsed = EvidenceBundle.model_validate(bundle())
    parsed.artifacts[0].path = "../../../etc/passwd"
    with pytest.raises(ValidationError, match="path"):
        parsed.canonical_bytes()


def test_digest_refuses_signed_integrity_without_a_signature() -> None:
    parsed = EvidenceBundle.model_validate(bundle())
    parsed.integrity = EvidenceIntegrity.SIGNED
    with pytest.raises(ValidationError, match="signature"):
        parsed.digest()


# --- everything that parses must be hashable -----------------------------------------


def test_integers_beyond_the_safe_range_are_rejected_at_parse_time() -> None:
    """Otherwise the failure surfaces later, as a canonicalization error at the gate."""
    with pytest.raises(ValidationError, match="less than or equal to"):
        MetricContract.model_validate(metric(budgets={"max_scanned_rows": 2**53}))
    with pytest.raises(ValidationError, match="less than or equal to"):
        MetricContract.model_validate(metric(metadata={**metric()["metadata"], "version": 10**20}))
    with pytest.raises(ValidationError, match="less than or equal to"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(size_bytes=2**53)]))


def test_empty_budgets_hash_the_same_as_no_budgets() -> None:
    absent = metric()
    absent.pop("budgets")
    without = MetricContract.model_validate(absent)
    empty = MetricContract.model_validate(metric(budgets={}))
    assert without.budgets is None
    assert empty.budgets is None
    assert empty.digest() == without.digest()


# --- path confinement ----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "checks/a\x00b.json",
        "checks/result.json:evil",
        "checks/foo.",
        "checks/foo ",
        "checks/ foo.json",
        "checks/con",
        "checks/con.json",
        "checks/nul.json",
        "checks/‮kcehc.json",
        "checks/naïve.json",
    ],
)
def test_artifact_path_segments_are_plain_names(path: str) -> None:
    with pytest.raises(ValidationError, match="path"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(path=path)]))


def test_ordinary_paths_still_pass() -> None:
    parsed = EvidenceBundle.model_validate(
        bundle(
            artifacts=[
                artifact(artifact_id="check-freshness", kind="check_result", path="checks/f-1.json")
            ],
            claims=[claim(evidence_refs=["check-freshness"])],
        )
    )
    assert parsed.artifacts[0].path == "checks/f-1.json"


# --- pins and blank text -------------------------------------------------------------


@pytest.mark.parametrize("revision", ["HEAD", "main", "master", "latest", "dev", "  ", ""])
def test_moving_pointers_are_not_pins(revision: str) -> None:
    with pytest.raises(ValidationError, match="pin"):
        ExternalFormula(source="dbt", ref="metric.shop.gm", source_revision=revision)


@pytest.mark.parametrize("revision", ["v1.2.3", "9d8c1f2a4b6e", "2026.08.1"])
def test_real_pins_are_accepted(revision: str) -> None:
    assert (
        ExternalFormula(
            source="dbt", ref="metric.shop.gm", source_revision=revision
        ).source_revision
        == revision
    )


def test_blank_text_is_not_content() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        MetricContract.model_validate(metric(metadata={**metric()["metadata"], "owner": "   "}))
    with pytest.raises(ValidationError, match="at least 1 character"):
        EvidenceBundle.model_validate(bundle(verifier_version=" "))
    with pytest.raises(ValidationError, match="at least 1 character"):
        EvidenceBundle.model_validate(bundle(artifacts=[artifact(media_type="  ")]))


# --- lists that are sets -------------------------------------------------------------


def test_scope_lists_reject_duplicates() -> None:
    with pytest.raises(ValidationError, match=r"duplicate entries in scope\.metrics"):
        DecisionProductSpec.model_validate(
            product(
                scope={**product()["scope"], "metrics": ["metric://sales/gross_margin_pct@3"] * 2}
            )
        )
    with pytest.raises(ValidationError, match=r"duplicate entries in scope\.dimensions"):
        DecisionProductSpec.model_validate(
            product(scope={**product()["scope"], "dimensions": ["region", "region"]})
        )
