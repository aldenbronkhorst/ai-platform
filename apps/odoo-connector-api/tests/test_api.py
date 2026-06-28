import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.core.odoo_client import (  # noqa: E402
    OdooClient,
    OdooCredentials,
    OdooError,
    OdooJsonRpcUnavailable,
    compact_odoo_jsonrpc_error,
)
from app.main import app  # noqa: E402

client = TestClient(app)

AUTH_HEADERS = {"X-Internal-API-Key": "test-internal-key"}
CREDENTIALS = {
    "url": "https://example.odoo.com",
    "db": "test",
    "username": "test",
    "api_key": "test",
}


def test_health_exposes_only_raw_odoo_capability():
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["capabilities"] == ["odoo.run"]


def test_capabilities_exposes_only_raw_odoo_endpoint():
    response = client.get("/capabilities", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["endpoints"] == [
        {
            "path": "/odoo/orm/run",
            "method": "POST",
            "description": "Run direct Odoo calls",
        }
    ]


def test_auto_transport_uses_jsonrpc_first():
    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="auto",
    )
    odoo.execute_kw_jsonrpc = MagicMock(return_value=True)
    odoo.execute_kw_xmlrpc = MagicMock()

    result = odoo.call_with_transport("account.move", "button_draft", args=[[56137]], kwargs={})

    assert result is True
    odoo.execute_kw_jsonrpc.assert_called_once_with("account.move", "button_draft", [[56137]], {})
    odoo.execute_kw_xmlrpc.assert_not_called()


def test_auto_transport_returns_jsonrpc_unavailable_without_xmlrpc_fallback():
    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="auto",
    )
    odoo.execute_kw_jsonrpc = MagicMock(side_effect=OdooJsonRpcUnavailable("not available"))
    odoo.execute_kw_xmlrpc = MagicMock(return_value=[{"id": 1, "name": "INV/001"}])

    with pytest.raises(OdooJsonRpcUnavailable):
        odoo.call_with_transport(
            "account.move",
            "search_read",
            args=[[["name", "=", "INV/001"]]],
            kwargs={"fields": ["id", "name"]},
        )

    odoo.execute_kw_jsonrpc.assert_called_once_with(
        "account.move",
        "search_read",
        [[["name", "=", "INV/001"]]],
        {"fields": ["id", "name"]},
    )
    odoo.execute_kw_xmlrpc.assert_not_called()


def test_auto_transport_uses_jsonrpc_for_report_methods():
    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="auto",
    )
    odoo.execute_kw_jsonrpc = MagicMock(return_value={"lines": []})
    odoo.execute_kw_xmlrpc = MagicMock()

    result = odoo.call_with_transport(
        "account.report",
        "get_options",
        args=[7, {"date": {"date_from": "2026-06-01", "date_to": "2026-06-30", "filter": "custom"}}],
        kwargs={},
    )

    assert result == {"lines": []}
    odoo.execute_kw_jsonrpc.assert_called_once_with(
        "account.report",
        "get_options",
        [7, {"date": {"date_from": "2026-06-01", "date_to": "2026-06-30", "filter": "custom"}}],
        {},
    )
    odoo.execute_kw_xmlrpc.assert_not_called()


def test_execute_kw_jsonrpc_authenticates_and_calls_through_jsonrpc():
    posted_payloads = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        responses = [
            FakeResponse({"result": 7}),
            FakeResponse({"result": [{"id": 1, "name": "INV/001"}]}),
        ]

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            posted_payloads.append(kwargs["json"])
            return self.responses.pop(0)

    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="jsonrpc",
    )
    odoo.common.authenticate = MagicMock(side_effect=AssertionError("XML-RPC auth should not be used"))

    with patch("app.core.odoo_client.httpx.Client", FakeClient):
        result = odoo.execute_kw_jsonrpc(
            "account.move",
            "search_read",
            args=[[["name", "=", "INV/001"]]],
            kwargs={"fields": ["id", "name"]},
        )

    assert result == [{"id": 1, "name": "INV/001"}]
    assert posted_payloads[0]["params"]["service"] == "common"
    assert posted_payloads[0]["params"]["method"] == "authenticate"
    assert posted_payloads[1]["params"]["service"] == "object"
    assert posted_payloads[1]["params"]["method"] == "execute_kw"


def test_jsonrpc_error_compacts_debug_traceback():
    error = {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {
            "name": "builtins.TypeError",
            "debug": (
                "Traceback (most recent call last):\n"
                "  File \"/odoo/http.py\", line 1, in _serve_db\n"
                "TypeError: AccountMove.js_assign_outstanding_line() missing 1 required positional argument: 'line_id'\n"
            ),
        },
    }

    message = compact_odoo_jsonrpc_error(error)

    assert message == "TypeError: AccountMove.js_assign_outstanding_line() missing 1 required positional argument: 'line_id'"
    assert "Traceback" not in message


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_runs_single_model_method(mock_get_client):
    mock_client = MagicMock()
    mock_client.call_with_transport.return_value = [{"id": 42, "name": "BNK01-2026-02065"}]
    mock_client.last_transport = "xmlrpc"
    mock_get_client.return_value = mock_client

    response = client.post(
        "/odoo/orm/run",
        json={
            "credentials": CREDENTIALS,
            "model": "account.move",
            "method": "search_read",
            "args": [[["name", "=", "BNK01-2026-02065"]]],
            "kwargs": {"fields": ["id", "name"], "limit": 1},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {
        "model": "account.move",
        "method": "search_read",
        "transport": "xmlrpc",
        "result": [{"id": 42, "name": "BNK01-2026-02065"}],
    }
    mock_client.call_with_transport.assert_called_once_with(
        "account.move",
        "search_read",
        args=[[["name", "=", "BNK01-2026-02065"]]],
        kwargs={"fields": ["id", "name"], "limit": 1},
    )


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_runs_batch(mock_get_client):
    mock_client = MagicMock()
    mock_client.call_with_transport.side_effect = [
        [{"id": 10, "line_ids": [100]}],
        [{"id": 100, "balance": 250.0}],
    ]
    mock_client.last_transport = "jsonrpc"
    mock_get_client.return_value = mock_client

    response = client.post(
        "/odoo/orm/run",
        json={
            "credentials": CREDENTIALS,
            "calls": [
                {
                    "name": "move",
                    "model": "account.move",
                    "method": "search_read",
                    "args": [[["name", "=", "BNK01-2026-02065"]]],
                    "kwargs": {"fields": ["id", "line_ids"], "limit": 1},
                },
                {
                    "name": "lines",
                    "model": "account.move.line",
                    "method": "read",
                    "args": [[100], ["id", "balance"]],
                },
            ],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["results"][0]["name"] == "move"
    assert data["results"][0]["result"] == [{"id": 10, "line_ids": [100]}]
    assert data["results"][1]["name"] == "lines"
    assert data["results"][1]["result"] == [{"id": 100, "balance": 250.0}]
    assert mock_client.call_with_transport.call_count == 2


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_batch_errors_are_sanitized(mock_get_client):
    mock_client = MagicMock()
    mock_client.call_with_transport.side_effect = RuntimeError("traceback with secret details")
    mock_get_client.return_value = mock_client

    response = client.post(
        "/odoo/orm/run",
        json={
            "credentials": CREDENTIALS,
            "continue_on_error": True,
            "calls": [
                {
                    "name": "failing_call",
                    "model": "account.move",
                    "method": "search_read",
                    "args": [[["name", "=", "BNK01-2026-02065"]]],
                },
            ],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    error_result = response.json()["results"][0]
    assert error_result["error"] is True
    assert error_result["error_type"] == "RuntimeError"
    assert error_result["message"] == "Odoo call failed."
    assert "secret" not in error_result["message"]


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_returns_structured_odoo_errors(mock_get_client):
    mock_client = MagicMock()
    mock_client.call_with_transport.side_effect = OdooError(
        "Odoo JSON-RPC returned a non-JSON response: HTTP 200 (text/html)"
    )
    mock_get_client.return_value = mock_client

    response = client.post(
        "/odoo/orm/run",
        json={
            "credentials": CREDENTIALS,
            "model": "res.users",
            "method": "search_count",
            "args": [[]],
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "odoo_call_failed"
    assert detail["error_type"] == "OdooError"
    assert detail["model"] == "res.users"
    assert detail["method"] == "search_count"
    assert "non-JSON response" in detail["message"]


def test_raw_odoo_endpoint_requires_model_and_method():
    response = client.post(
        "/odoo/orm/run",
        json={"credentials": CREDENTIALS, "model": "account.move"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "odoo_call_requires_model_and_method"
