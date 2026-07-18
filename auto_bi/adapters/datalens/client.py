"""Thin DataLens client: signin (cookie session) + UI-gateway JSON RPC.

Transport is verified live on the self-hosted OSS stand (reversal doc
`docs/plans/2026-06-13-phase3.2-datalens-adapter-reversal.md` §1-2): the adapter talks to
the **UI gateway** with a **cookie session** named ``auth``. Two route shapes:
- signin: ``POST {base}/gateway/auth/auth/signin`` (scope literally ``auth``, served by a
  dedicated route with AuthPolicy.disabled so it works unauthenticated) — `{login,
  password}` -> 200 `{"done":true}` + Set-Cookie ``auth``;
- RPC: ``POST {base}/gateway/root/<service>/<method>`` (scope ``root``) for the
  authenticated calls (bi/*, us/*), carrying the ``auth`` cookie.
The gateway decrypts the cookie itself before forwarding to the internal backends
(control-api/us/auth/data-api); in a self-hosted deploy the UI is the only public surface.

`bi/createConnection` and `bi/createDataset` were both confirmed to forward their body
correctly through the gateway; only `bi/validateDataset` drops the body (415), which is
why the dataset schema is built deterministically from the IR (see dataset.py) instead
of via validate.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Live-verified on the stand (2026-06-14): scope is literally `auth`, not `root` — the
# auth gateway is a dedicated route `POST /gateway/:scope(auth)/:service/:action` with
# AuthPolicy.disabled. `/gateway/root/auth/signin` returns 401 (root scope is auth-gated).
DEFAULT_SIGNIN_PATH = "/gateway/auth/auth/signin"


class DataLensAPIError(Exception):
    """API-level failure; `status_code` carries the HTTP status when one was received
    (None for signin-shape failures), so callers like `delete_artifact` can tell an
    already-gone 404 from a real error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
                f"{service}/{method} -> {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    def post(self, path: str, body: dict) -> dict[str, Any]:
        """POST to a non-gateway endpoint (e.g. the charts engine `/api/charts/v1/charts`
        or `/api/run`), carrying the `auth` cookie. Logs in first if needed."""
        if not self._logged_in:
            self.login()
        response = self._http.post(path, json=body)
        if response.status_code == 401:
            self.login()
            response = self._http.post(path, json=body)
        if response.status_code >= 400:
            raise DataLensAPIError(
                f"POST {path} -> {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    def health(self, path: str = "/ping") -> bool:
        """Liveness only: ``/ping`` answers 200 whenever the UI process is up — it does NOT
        prove a valid session, gateway forwarding, or workbook access. The adapter's
        ``healthcheck`` adds a cheap *authorized* gateway call on top of this (F6)."""
        try:
            return self._http.get(path).status_code == 200
        except httpx.HTTPError:
            return False
