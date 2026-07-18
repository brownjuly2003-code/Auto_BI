"""C-8: no shipped 'admin' default for the DataLens password — empty fails loudly."""

import pytest

from auto_bi.adapters.datalens.client import DataLensAPIError, DataLensClient
from auto_bi.config import Settings


def test_settings_default_is_empty() -> None:
    assert Settings.model_fields["datalens_password"].default == ""


def test_login_with_empty_password_raises_clear_error() -> None:
    class _NoNetwork:
        def post(self, *a, **k):  # pragma: no cover — must never be reached
            raise AssertionError("network call attempted with empty password")

    client = DataLensClient("http://dl", "admin", "", http=_NoNetwork())
    with pytest.raises(DataLensAPIError, match="AUTO_BI_DATALENS_PASSWORD"):
        client.login()
