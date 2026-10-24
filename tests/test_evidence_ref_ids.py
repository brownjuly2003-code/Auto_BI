"""The ids of the claim of an equivalent result and of a compiled BI payload, through the gate.

Durable spec: docs/specs/evidence-ref-ids.md. Both ids were the slug of a ref, which folds `_`,
`-`, `/` and `@` into one `-`: `asset://sales/weekly_margin_by_region` and
`asset://sales/weekly-margin-by-region`, or `dashboard://weekly_margin@7` and
`dashboard://weekly-margin@7`, gave one id, and the run ended after grading in a pydantic
traceback out of `EvidenceBundle`, with no bundle and exit 1. The id now ends in the full SHA-256
of the canonical array of the ref (and the compiler's id of the payload) after that slug.
"""

import copy
import re
from pathlib import Path
from typing import Any

import pytest

from auto_bi.cli import main
from auto_bi.evidence.canonical import sha256_digest
from auto_bi.evidence.manifest import read_bundle, verify_bundle
from auto_bi.evidence.model import EvidenceBundle
from auto_bi.evidence.store import FilesystemArtifactStore
from auto_bi.verification.model import Verdict
from auto_bi.verification.release_gate import VERIFIER_VERSION, GateReport
from tests.test_decision_cli import _verify_argv
from tests.test_decision_verify import (
    ASSET_ID,
    RESULT_COLUMNS,
    Case,
    _check_payload,
    _write_yaml,
    attestation,
    dashboard_spec,
    product_contract,
    verified_asset,
)
from tests.test_unreadable_run_inputs import ROWS

DASHBOARD = "dashboard://weekly_margin@7"
HYPHENED = "dashboard://weekly-margin@7"
#: The compiler's ids of the payloads of `dashboard_spec()`.
LOCAL_IDS = ["chart-1", "dashboard", "dataset-1"]
PILOT = Path(__file__).resolve().parents[1] / "docs" / "pilot" / "2026-08-25-ch-control"
_HEX = re.compile(r"-[0-9a-f]{64}\Z")


def _hex(*identity: str) -> str:
    return sha256_digest(list(identity)).removeprefix("sha256:")


def claim_id(asset_id: str, slug: str) -> str:
    return f"claim-{slug}-{_hex(asset_id)}"


def bi_id(dashboard_ref: str, slug: str, local_id: str) -> str:
    return f"bi-{slug}-{local_id}-{_hex(dashboard_ref, local_id)}"


def _with_assets(case: Case, *asset_ids: str) -> Case:
    """One equivalent result for each of *asset_ids*: copies of the fixture's asset."""
    case.assets = case.root / "assets"
    result = {"columns": RESULT_COLUMNS, "rows": ROWS}
    recorded: dict[str, Any] = {}
    for index, asset_id in enumerate(asset_ids):
        asset = verified_asset(case.base_digest)
        asset["metadata"]["id"] = asset_id
        _write_yaml(case.assets / f"asset_{index}.yaml", asset)
        recorded[asset_id] = {"result": result, "attestation": attestation(case.candidate_digest)}
    case.results = case.root / "results.yaml"
    _write_yaml(case.results, recorded)
    return case


def _delivering(case: Case, *refs: str) -> Case:
    product = product_contract()
    product["delivery"]["dashboards"] = [{"ref": ref} for ref in refs]
    _write_yaml(case.contracts / "weekly_margin.yaml", product)
    return case


def _bundle(tmp_path: Path, report: GateReport) -> EvidenceBundle:
    store = FilesystemArtifactStore(tmp_path / "evidence")
    assert verify_bundle(store, report.run_id) == []
    return read_bundle(store, report.run_id)


def _bi_digests(tmp_path: Path, report: GateReport) -> dict[str, str]:
    bundle = _bundle(tmp_path, report)
    return {a.artifact_id: a.digest for a in bundle.artifacts if a.kind.value == "bi_contract"}


def _compiled(tmp_path: Path, report: GateReport) -> dict[str, dict[str, str]]:
    """The digest of each payload `bi.contract` compiled, by dashboard and compiler id."""
    payload = _check_payload(tmp_path, report, "bi.contract")
    return {
        item["dashboard"]: {a["artifact_id"]: a["digest"] for a in item["artifacts"]}
        for item in payload["compiled"]
    }


def _expected(tmp_path: Path, report: GateReport, slugs: dict[str, str]) -> dict[str, str]:
    """The id and digest of every BI payload the bundle should hold, from `bi.contract`."""
    return {
        bi_id(ref, slugs[ref], local): digest
        for ref, payloads in _compiled(tmp_path, report).items()
        for local, digest in payloads.items()
    }


# --- the defect: two refs of one slug -----------------------------------------------------


@pytest.mark.parametrize(
    "other",
    ["asset://sales/weekly-margin-by-region", "asset://sales/weekly/margin_by-region"],
    ids=["underscore", "slash"],
)
def test_two_assets_of_one_slug_both_seal_their_claim(tmp_path: Path, other: str) -> None:
    report = _with_assets(Case(tmp_path), ASSET_ID, other).run()

    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    claims = _bundle(tmp_path, report).claims
    slug = "asset-sales-weekly-margin-by-region"
    assert sorted(claim.claim_id for claim in claims) == sorted(
        [claim_id(ASSET_ID, slug), claim_id(other, slug)]
    )
    assert {claim.summary_code for claim in claims} == {"result.equivalent"}
    comparisons = _check_payload(tmp_path, report, "results.reconcile")["comparisons"]
    assert sorted(item["asset"] for item in comparisons) == sorted([f"{ASSET_ID}@2", f"{other}@2"])
    for claim in claims:
        assert claim.evidence_refs == claims[0].evidence_refs
        assert "check-results.reconcile" in claim.evidence_refs


@pytest.mark.parametrize("alike", [True, False], ids=["same-payloads", "other-payloads"])
def test_two_dashboards_of_one_slug_both_seal_their_payloads(tmp_path: Path, alike: bool) -> None:
    case = _delivering(Case(tmp_path).with_asset().with_results(ROWS), DASHBOARD, HYPHENED)
    other = copy.deepcopy(dashboard_spec())
    if not alike:
        other["title"] = "Маржа по регионам, дефис"
    _write_yaml(case.dashboards / "weekly-margin@7.yaml", other)

    report = case.run()

    assert report.verdict is Verdict.PASS_WITH_ADVISORY
    compiled = _compiled(tmp_path, report)
    assert sorted(compiled) == sorted([DASHBOARD, HYPHENED])
    assert (compiled[DASHBOARD] == compiled[HYPHENED]) is alike
    slug = "dashboard-weekly-margin-7"
    stored = _bi_digests(tmp_path, report)
    assert stored == _expected(tmp_path, report, {DASHBOARD: slug, HYPHENED: slug})
    assert len(stored) == 2 * len(LOCAL_IDS)


def test_the_cli_writes_the_bundle_of_two_assets_of_one_slug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _with_assets(Case(tmp_path), ASSET_ID, "asset://sales/weekly-margin-by-region")
    (case.dashboards / "weekly_margin@7.yaml").unlink()

    assert main(_verify_argv(case)) == 3

    out = capsys.readouterr().out
    assert "verdict: needs_review" in out
    (run,) = (tmp_path / "evidence").iterdir()
    assert main(["decision", "evidence", str(tmp_path / "evidence"), "--run", run.name]) == 0
    listed = capsys.readouterr().out
    slug = "asset-sales-weekly-margin-by-region"
    assert claim_id(ASSET_ID, slug) in listed
    assert claim_id("asset://sales/weekly-margin-by-region", slug) in listed


# --- what the id is made of ---------------------------------------------------------------


def test_the_ids_are_a_slug_to_read_then_the_full_hash(tmp_path: Path) -> None:
    report = Case(tmp_path).with_asset().with_results(ROWS).run()

    bundle = _bundle(tmp_path, report)
    (claim,) = bundle.claims
    assert claim.claim_id == (
        "claim-asset-sales-weekly-margin-by-region-" + _hex("asset://sales/weekly_margin_by_region")
    )
    stored = sorted(a.artifact_id for a in bundle.artifacts if a.kind.value == "bi_contract")
    assert stored == sorted(
        f"bi-dashboard-weekly-margin-7-{local}-{_hex(DASHBOARD, local)}" for local in LOCAL_IDS
    )
    assert all(_HEX.search(item) for item in [claim.claim_id, *stored])


@pytest.mark.parametrize(
    ("ref", "parts", "other", "other_parts"),
    [
        ("dashboard://sales/weekly_margin@7", (), "dashboard://sales-weekly_margin@7", ()),
        ("dashboard://weekly_margin@7", (), "dashboard://weekly_margin-7", ()),
        ("dashboard://weekly-margin@7", (), "dashboard://weekly--margin@7", ()),
        ("dashboard://weekly_margin@7", ("chart-1",), "dashboard://weekly_margin@7-chart", ("1",)),
    ],
    ids=["namespace", "pin", "repeated-separator", "boundary"],
)
def test_the_hash_tells_apart_what_the_slug_folds(
    ref: str, parts: tuple[str, ...], other: str, other_parts: tuple[str, ...]
) -> None:
    from auto_bi.verification.release_gate import _ref_id

    first, second = _ref_id("bi", ref, *parts), _ref_id("bi", other, *other_parts)
    assert _HEX.sub("", first) == _HEX.sub("", second)
    assert first != second
    assert first.endswith(_hex(ref, *parts))
    assert second.endswith(_hex(other, *other_parts))


def test_a_payload_id_does_not_depend_on_the_other_dashboards_or_their_order(
    tmp_path: Path,
) -> None:
    ids = []
    for run, refs in enumerate([(DASHBOARD,), (HYPHENED, DASHBOARD), (DASHBOARD, HYPHENED)]):
        case = _delivering(Case(tmp_path / f"run-{run}").with_asset().with_results(ROWS), *refs)
        _write_yaml(case.dashboards / "weekly-margin@7.yaml", dashboard_spec())
        stored = _bi_digests(tmp_path / f"run-{run}", case.run())
        ids.append({item: digest for item, digest in stored.items() if "-weekly-margin-7-" in item})

    own = {bi_id(DASHBOARD, "dashboard-weekly-margin-7", local) for local in LOCAL_IDS}
    for stored in ids:
        assert {item: stored[item] for item in own} == {item: ids[0][item] for item in own}


def test_a_payload_that_changes_keeps_its_id_and_moves_its_digest(tmp_path: Path) -> None:
    stored = []
    for run, title in enumerate(["Маржа", "Маржа, другая"]):
        case = Case(tmp_path / f"run-{run}").with_asset().with_results(ROWS)
        spec = dashboard_spec()
        spec["title"] = title
        _write_yaml(case.dashboards / "weekly_margin@7.yaml", spec)
        stored.append(_bi_digests(tmp_path / f"run-{run}", case.run()))

    assert sorted(stored[0]) == sorted(stored[1])
    moved = {item for item in stored[0] if stored[0][item] != stored[1][item]}
    assert bi_id(DASHBOARD, "dashboard-weekly-margin-7", "dashboard") in moved


# --- what is not changed ------------------------------------------------------------------


def test_a_bundle_sealed_under_slug_ids_still_reads() -> None:
    store = FilesystemArtifactStore(PILOT / "evidence")
    (run,) = (path.name for path in (PILOT / "evidence").iterdir())

    assert verify_bundle(store, run) == []
    bundle = read_bundle(store, run)
    assert [claim.claim_id for claim in bundle.claims] == [
        "claim-asset-sales-weekly-margin-by-region"
    ]
    assert "bi-dashboard-weekly-margin-7-dataset-1" in {a.artifact_id for a in bundle.artifacts}


def test_naming_the_ids_by_hash_moved_the_verifier_version() -> None:
    assert int(VERIFIER_VERSION) >= 113
