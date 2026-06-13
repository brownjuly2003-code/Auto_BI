"""Thin DataLens client: signin (cookie session) + UI-gateway JSON RPC.

Transport is the one verified live on the self-hosted OSS stand (reversal doc
`docs/plans/2026-06-13-phase3.2-datalens-adapter-reversal.md` §1-2): the adapter
talks to the **UI gateway** at ``POST {base}/gateway/root/<service>/<method>`` with a
**cookie session** named ``auth`` (a JWE the gateway decrypts itself before forwarding
``Authorization: Bearer <JWS>`` to the internal backends). In a self-hosted deploy the
UI is the only public surface; control-api/us/auth/data-api are internal.

`bi/createConnection` and `bi/createDataset` were both confirmed to forward their body
correctly through the gateway; only `bi/validateDataset` drops the body (415), which is
why the dataset schema is built deterministically from the IR (see dataset.py) instead
of via validate.

OPEN (reversal §5.1): the exact public signin route is not yet pinned — `/signin` lives
on the internal auth:8080; through the public gateway the tried paths returned 404/401.
`login()` posts to ``signin_path`` (overridable); the live contract test pins it. The
cookie-jar + gateway transport below is what was verified, not this default path.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# best-known candidate; pinned by the live contract test (reversal §5.1)
DEFAULT_SIGNIN_PATH = "/gateway/root/auth/signin"


class DataLensAPIError(Exception):
    pass


class DataLensClient:
    """Cookie-session client over the DataLens UI gateway (mirror of SupersetClient)."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        http: httpx.Client | None = None,
        *,
        signin_path: str = DEFAULT_SIGNIN_PATH,
    ) -> None:
        self._username = username
        self._password = password
        # httpx.Client carries its own cookie jar across requests, so the `auth` cookie
        # set by signin is automatically attached to every later /gateway/root/* call.
        self._http = http or httpx.Client(
            base_url=base_url,
            timeout=60.0,
            transport=httpx.HTTPTransport(retries=2),  # transient connect failures only
        )
        self._signin_path = signin_path
        self._logged_in = False

    def login(self) -> None:
        response = self._http.post(
            self._signin_path,
            json={"login": self._username, "password": self._password},
        )
        if response.status_code != 200:
            raise DataLensAPIError(f"signin failed: {response.status_code} {response.text[:300]}")
        if "auth" not in self._http.cookies:
            # signin returned 200 but no session cookie -> wrong route/shape (open item)
            raise DataLensAPIError("signin ok but no `auth` cookie set; check signin_path")
        self._logged_in = True
        logger.info("datalens signin ok")

    def gateway(self, service: str, method: str, body: dict) -> dict[str, Any]:
        """One UI-gateway RPC, e.g. gateway("bi", "createConnection", {...})."""
        if not self._logged_in:
            self.login()
        path = f"/gateway/root/{service}/{method}"
        response = self._http.post(path, json=body)
        if response.status_code == 401:  # session expired -> one re-login
            self.login()
            response = self._http.post(path, json=body)
        if response.status_code >= 400:
            raise DataLensAPIError(
                f"{service}/{method} -> {response.status_code}: {response.text[:500]}"
            )
        return response.json() if response.content else {}

    def health(self, path: str = "/ping") -> bool:
        try:
            return self._http.get(path).status_code == 200
        except httpx.HTTPError:
            return False
