"""Regression: connector error-code disambiguation in Odoo credential verification.

A connector 401 with detail.error='odoo_auth_failed' means the user's Odoo
credentials are wrong -> report as invalid credentials (400). A 401 with a plain
string detail means an internal connector-key mismatch (401). The connector nests
its HTTPException payload under 'detail', so that wrapper must be unwrapped, or a
wrong Odoo key gets misreported as an internal admin/config problem.
"""
import os

os.environ.setdefault("ODOO_CONNECTOR_URL", "http://mock-connector:8000")
os.environ.setdefault("ODOO_CONNECTOR_API_KEY", "test-key")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.routers.connected_accounts import _raise_verify_response_error  # noqa: E402


def test_connector_401_odoo_auth_failed_is_reported_as_invalid_credentials():
    resp = httpx.Response(
        401,
        json={
            "detail": {
                "error": "odoo_auth_failed",
                "error_type": "OdooAuthError",
                "message": "Odoo authentication failed for the linked user.",
                "model": "res.partner",
                "method": "search_read",
            }
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        _raise_verify_response_error(resp)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error_type"] != "odoo_connector_auth_failed"
    assert "credentials are invalid" in exc_info.value.detail["message"].lower()


def test_connector_401_plain_detail_is_internal_key_mismatch():
    resp = httpx.Response(401, json={"detail": "Invalid internal API key"})
    with pytest.raises(HTTPException) as exc_info:
        _raise_verify_response_error(resp)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error_type"] == "odoo_connector_auth_failed"


def test_connector_400_odoo_auth_failed_still_maps_to_invalid_credentials():
    # backward-compatibility: even if the connector reported auth as 400
    resp = httpx.Response(400, json={"detail": {"error": "odoo_auth_failed", "message": "bad key"}})
    with pytest.raises(HTTPException) as exc_info:
        _raise_verify_response_error(resp)
    assert exc_info.value.status_code == 400
    assert "credentials are invalid" in exc_info.value.detail["message"].lower()
