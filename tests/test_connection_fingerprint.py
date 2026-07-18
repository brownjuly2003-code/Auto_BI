"""C-6: on connection reuse the stored fingerprint (host/port/db) must match the
current DWHConfig — a stale entry silently feeds dashboards from the wrong DWH.
Mismatch warns by default and refuses under strict_connection."""

import json
import logging

import httpx
import pytest

from auto_bi.adapters.base import DWHConfig
from auto_bi.adapters.datalens.adapter import DataLensAdapter
from auto_bi.adapters.datalens.client import DataLensAPIError, DataLensClient
from auto_bi.adapters.superset.adapter import SupersetAdapter
from auto_bi.adapters.superset.client import SupersetAPIError, SupersetClient
from auto_bi.semantic.model import SemanticModel

DWH = DWHConfig(host="ch", port=8123, database="dm", user="ro", password="pw")
MODEL = SemanticModel.load("semantic/model.yaml")


class FakeSupersetWithDetail:
    """Login + database list/detail, parameterized by the stored sqlalchemy_uri."""

    def __init__(self, uri: str | None) -> None:
        self.uri = uri

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/security/login":
            return httpx.Response(200, json={"access_token": "jwt"})
        if path == "/api/v1/security/csrf_token/":
            return httpx.Response(200, json={"result": "csrf"})
        if path == "/api/v1/database/" and request.method == "GET":
            return httpx.Response(200, json={"result": [{"id": 5, "database_name": "auto_bi"}]})
        if path == "/api/v1/database/5" and request.method == "GET":
            payload = {} if self.uri is None else {"sqlalchemy_uri": self.uri}
            return httpx.Response(200, json={"result": payload})
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _superset(uri: str | None, *, strict: bool = False) -> SupersetAdapter:
    http = httpx.Client(
        base_url="http://superset.test", transport=httpx.MockTransport(FakeSupersetWithDetail(uri))
    )
    client = SupersetClient("http://superset.test", "admin", "pw", http=http)
    return SupersetAdapter(client, DWH, MODEL, strict_connection=strict)


MATCHING = "clickhousedb://ro:XXXXXXXXXX@ch:8123/dm"  # password masked, as Superset returns it
STALE = "clickhousedb://ro:XXXXXXXXXX@old-stand:9999/other"


def test_superset_matching_fingerprint_is_silent(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        ref = _superset(MATCHING).ensure_database()
    assert ref.id == 5
    assert not [r for r in caplog.records if "does not match" in r.getMessage()]


def test_superset_stale_fingerprint_warns_but_proceeds(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        ref = _superset(STALE).ensure_database()
    assert ref.id == 5  # advisory by default: reuse continues
    warning = [r for r in caplog.records if "does not match" in r.getMessage()]
    assert warning and "old-stand" in warning[0].getMessage()


def test_superset_stale_fingerprint_refused_when_strict() -> None:
    with pytest.raises(SupersetAPIError, match="stale BI connection refused"):
        _superset(STALE, strict=True).ensure_database()


def test_superset_unreadable_fingerprint_never_blocks(caplog) -> None:
    # detail without a uri -> cannot verify -> silent reuse even under strict
    with caplog.at_level(logging.WARNING):
        ref = _superset(None, strict=True).ensure_database()
    assert ref.id == 5
    assert not [r for r in caplog.records if "does not match" in r.getMessage()]


class FakeDataLens:
    """Signin + workbook entry lookup + getConnection echo."""

    def __init__(self, host: str | None, port: int | None) -> None:
        self.host, self.port = host, port

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gateway/auth/auth/signin":
            return httpx.Response(
                200, json={"done": True}, headers={"set-cookie": "auth=tok; Path=/"}
            )
        if path == "/gateway/root/us/getWorkbookEntries":
            return httpx.Response(
                200,
                json={"entries": [{"entryId": "conn1", "key": "wb/Auto_BI ClickHouse"}]},
            )
        if path == "/gateway/root/bi/getConnection":
            body = json.loads(request.content)
            assert body["connectionId"] == "conn1"
            payload: dict = {}
            if self.host is not None:
                payload["host"] = self.host
            if self.port is not None:
                payload["port"] = self.port
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _datalens(host: str | None, port: int | None, *, strict: bool = False) -> DataLensAdapter:
    http = httpx.Client(
        base_url="http://dl.test", transport=httpx.MockTransport(FakeDataLens(host, port))
    )
    client = DataLensClient("http://dl.test", "admin", "pw", http=http)
    return DataLensAdapter(client, DWH, MODEL, "wb", strict_connection=strict)


def test_datalens_matching_fingerprint_is_silent(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        ref = _datalens("ch", 8123).ensure_database()
    assert ref.id == "conn1"
    assert not [r for r in caplog.records if "does not match" in r.getMessage()]


def test_datalens_stale_fingerprint_warns_but_proceeds(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        ref = _datalens("old-stand", 9999).ensure_database()
    assert ref.id == "conn1"
    warning = [r for r in caplog.records if "does not match" in r.getMessage()]
    assert warning and "old-stand" in warning[0].getMessage()


def test_datalens_stale_fingerprint_refused_when_strict() -> None:
    with pytest.raises(DataLensAPIError, match="stale BI connection refused"):
        _datalens("old-stand", 9999, strict=True).ensure_database()


def test_datalens_unknown_shape_never_blocks() -> None:
    # a getConnection response without host (shape drift) -> cannot verify -> reuse
    ref = _datalens(None, None, strict=True).ensure_database()
    assert ref.id == "conn1"
