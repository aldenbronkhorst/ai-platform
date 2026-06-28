import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.core.odoo_client import (  # noqa: E402
    OdooClient,
    OdooCredentials,
    OdooJson2Unavailable,
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


def test_health_exposes_only_raw_orm_capability():
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["capabilities"] == ["odoo.orm.run"]


def test_capabilities_exposes_only_raw_orm_endpoint():
    response = client.get("/capabilities", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["endpoints"] == [
        {
            "path": "/odoo/orm/run",
            "method": "POST",
            "description": "Run direct Odoo ORM calls",
        }
    ]


def test_auto_transport_uses_json2_first():
    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="auto",
    )
    odoo.execute_kw_json2 = MagicMock(return_value=None)
    odoo.execute_kw_xmlrpc = MagicMock()

    result = odoo.call_with_transport("account.move", "button_draft", args=[[56137]], kwargs={})

    assert result is None
    odoo.execute_kw_json2.assert_called_once_with("account.move", "button_draft", [[56137]], {}, json2_payload=None)
    odoo.execute_kw_xmlrpc.assert_not_called()


def test_auto_transport_falls_back_to_jsonrpc_when_json2_unavailable():
    odoo = OdooClient(
        OdooCredentials(
            url="https://example.odoo.com",
            db="test",
            username="test",
            password_or_api_key="test",
        ),
        transport="auto",
    )
    odoo.execute_kw_json2 = MagicMock(side_effect=OdooJson2Unavailable("not available"))
    odoo.execute_kw_jsonrpc = MagicMock(return_value=[{"id": 1, "name": "INV/001"}])
    odoo.execute_kw_xmlrpc = MagicMock()

    result = odoo.call_with_transport(
        "account.move",
        "search_read",
        args=[[["name", "=", "INV/001"]]],
        kwargs={"fields": ["id", "name"]},
    )

    assert result == [{"id": 1, "name": "INV/001"}]
    odoo.execute_kw_jsonrpc.assert_called_once_with(
        "account.move",
        "search_read",
        [[["name", "=", "INV/001"]]],
        {"fields": ["id", "name"]},
    )
    odoo.execute_kw_xmlrpc.assert_not_called()


def test_auto_transport_uses_jsonrpc_for_report_methods_json2_cannot_infer():
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
def test_raw_orm_endpoint_runs_single_model_method(mock_get_client):
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
        json2_payload=None,
    )


@patch("app.routers.orm_runner._get_client")
def test_raw_orm_endpoint_runs_batch(mock_get_client):
    mock_client = MagicMock()
    mock_client.call_with_transport.side_effect = [
        [{"id": 10, "line_ids": [100]}],
        [{"id": 100, "balance": 250.0}],
    ]
    mock_client.last_transport = "json2"
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
def test_raw_orm_endpoint_batch_errors_are_sanitized(mock_get_client):
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
    assert error_result["message"] == "Odoo ORM call failed."
    assert "secret" not in error_result["message"]


def test_raw_orm_endpoint_requires_model_and_method():
    response = client.post(
        "/odoo/orm/run",
        json={"credentials": CREDENTIALS, "model": "account.move"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "orm_call_requires_model_and_method"
