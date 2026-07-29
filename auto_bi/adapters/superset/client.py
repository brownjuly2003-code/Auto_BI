"""Thin Superset REST client: JWT login + CSRF, JSON requests, list filters.

Pinned against Superset 4.1 (docker/superset/Dockerfile); endpoint drift is caught
by the contract tests on the live stand, not here.

Provider response bodies never enter exception messages (plan_sol step 3 / audit
P0-3): status code + path are enough for callers; bodies are redacted at debug log.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from auto_bi.errors import redact_secrets

logger = logging.getLogger(__name__)


class SupersetAPIError(Exception):
    """API-level failure; `status_code` carries the HTTP status when one was received
    (None for login/CSRF failures raised before a request cycle completes), so callers
    like `delete_artifact` can tell an already-gone 404 from a real error.

    ``message`` must stay free of response bodies and credentials — use
    :func:`auto_bi.errors.to_safe_error` at API/store boundaries.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def rison_eq_filter(
    column: str,
    value: str,
    page_size: int = 100,
    *,
    page: int | None = None,
) -> str:
    """Minimal rison for the only list-filter shape we use."""
    escaped = value.replace("'", "''")
    page_part = f",page:{page}" if page is not None else ""
    return (
        f"(filters:!((col:{column},opr:eq,value:'{escaped}'))" f"{page_part},page_size:{page_size})"
    )


class SupersetClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._http = http or httpx.Client(
            base_url=base_url,
            timeout=60.0,
            transport=httpx.HTTPTransport(retries=2),  # transient connect failures only
        )
        self._access_token: str | None = None
        self._csrf_token: str | None = None

    def close(self) -> None:
        """Release the underlying httpx connection pool (long-lived callers/tests)."""
        self._http.close()

    def login(self) -> None:
        response = self._http.post(
            "/api/v1/security/login",
            json={
                "username": self._username,
                "password": self._password,
                "provider": "db",
                "refresh": True,
            },
        )
        if response.status_code != 200:
            logger.debug(
                "superset login failed HTTP %s",
                response.status_code,
            )
            raise SupersetAPIError(
                f"login failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        self._access_token = response.json()["access_token"]

        csrf = self._http.get("/api/v1/security/csrf_token/", headers=self._auth_headers())
        if csrf.status_code != 200:
            logger.debug(
                "superset csrf fetch failed HTTP %s",
                csrf.status_code,
            )
            raise SupersetAPIError(
                f"csrf fetch failed: HTTP {csrf.status_code}",
                status_code=csrf.status_code,
            )
        self._csrf_token = csrf.json()["result"]
        logger.info("superset login ok")

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if self._csrf_token:
            headers["X-CSRFToken"] = self._csrf_token
            headers["Referer"] = str(self._http.base_url)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._access_token is None:
            self.login()
        response = self._http.request(
            method, path, json=json, params=params, headers=self._auth_headers()
        )
        if response.status_code == 401:  # token expired -> one re-login
            self.login()
            response = self._http.request(
                method, path, json=json, params=params, headers=self._auth_headers()
            )
        if response.status_code >= 400:
            logger.debug(
                "superset %s %s failed HTTP %s body=%s",
                method,
                path,
                response.status_code,
                redact_secrets(response.text[:500]),
            )
            raise SupersetAPIError(
                f"{method} {path} -> HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response.json() if response.content else {}

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", path, params=params)

    def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", path, json=json)

    def put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        return self.request("PUT", path, json=json)

    def delete(self, path: str) -> dict[str, Any]:
        return self.request("DELETE", path)

    def health(self) -> bool:
        try:
            return self._http.get("/health").status_code == 200
        except httpx.HTTPError:
            return False
