"""F9: OdooClient retry behaviour on a flaky/down backend (retry exhaustion and
non-transient errors) - the branches operators hit when Odoo.sh is redeploying."""
from unittest.mock import patch

import httpx
import pytest

from app.core.odoo_client import OdooClient, OdooCredentials, OdooJsonRpcUnavailable

_REQUEST = httpx.Request("POST", "https://example.odoo.com/jsonrpc")


def _creds():
    return OdooCredentials(url="https://example.odoo.com", db="test", username="test", password_or_api_key="test")


def _status_response(status_code):
    class _Resp:
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                f"{status_code}", request=_REQUEST, response=httpx.Response(status_code, request=_REQUEST)
            )

    return _Resp()


def _fake_client(status_code, counter):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            counter["n"] += 1
            return _status_response(status_code)

    return _FakeClient


def test_transient_status_retries_then_raises_unavailable():
    counter = {"n": 0}
    odoo = OdooClient(_creds(), max_attempts=3, retry_backoff_seconds=0)
    with patch("app.core.odoo_client.httpx.Client", _fake_client(503, counter)):
        with pytest.raises(OdooJsonRpcUnavailable):
            odoo.authenticate()
    assert counter["n"] == 3  # 503 is transient -> retried up to max_attempts


def test_non_transient_status_is_not_retried():
    counter = {"n": 0}
    odoo = OdooClient(_creds(), max_attempts=3, retry_backoff_seconds=0)
    with patch("app.core.odoo_client.httpx.Client", _fake_client(500, counter)):
        with pytest.raises(OdooJsonRpcUnavailable):
            odoo.authenticate()
    assert counter["n"] == 1  # 500 is not in the transient set -> single attempt
