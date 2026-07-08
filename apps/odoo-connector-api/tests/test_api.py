import os
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.core.odoo_client import (  # noqa: E402
    OdooClient,
    OdooCredentials,
    OdooError,
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
    assert data["capabilities"] == ["odoo.run", "odoo.guidance"]


def test_capabilities_exposes_only_raw_odoo_endpoint():
    response = client.get("/capabilities", headers=AUTH_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert data["endpoints"] == [
        {
            "path": "/odoo/orm/run",
            "method": "POST",
            "description": "Run direct Odoo calls",
        },
        {
            "path": "/odoo/guidance",
            "method": "GET",
            "description": "Return Odoo connector guidance",
        },
        {
            "path": "/odoo/manifest",
            "method": "GET",
            "description": "Return Odoo connector package manifest",
        },
    ]
    assert data["guidance_version"] == "2.4.0"


def test_connector_serves_its_own_manifest_and_skill():
    manifest_response = client.get("/odoo/manifest", headers=AUTH_HEADERS)
    guidance_response = client.get("/odoo/guidance", headers=AUTH_HEADERS)

    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["id"] == "odoo"
    assert manifest["broker_target"] == "odoo"
    assert manifest["skills"][0]["path"] == "skills/odoo-api/SKILL.md"

    assert guidance_response.status_code == 200
    guidance = guidance_response.json()
    assert guidance["connector"] == "odoo"
    assert guidance["version"] == "2.4.0"
    assert guidance["source"].endswith("apps/odoo-connector-api/skills/odoo-api/SKILL.md")
    assert "Direct integration with Odoo ERP via JSON-RPC" in guidance["content"]
    assert "call(\"odoo\"" in guidance["content"]


def test_raw_odoo_endpoint_can_return_connector_guidance():
    response = client.post(
        "/odoo/orm/run",
        json={"operation": "guidance"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["connector"] == "odoo"
    assert data["manifest"]["id"] == "odoo"


def test_guidance_lists_fetchable_troubleshooting_documents():
    guidance = client.get("/odoo/guidance", headers=AUTH_HEADERS).json()

    documents = guidance["documents"]
    assert "00-diagnostic-loop" in documents
    assert "01-symptom-router" in documents
    for playbook in (
        "records-missing",
        "access-denied",
        "report-numbers-wrong",
        "write-failed",
        "performance-timeout",
        "sequence-journal",
        "data-integrity",
    ):
        assert playbook in documents
    assert "playbook" in guidance["operations"]


def test_playbook_operation_returns_a_document():
    response = client.post(
        "/odoo/orm/run",
        json={"operation": "playbook", "name": "records-missing"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["operation"] == "playbook"
    assert data["name"] == "records-missing"
    assert data["format"] == "markdown"
    assert "Records Missing" in data["content"]


def test_playbook_operation_needs_no_credentials():
    response = client.post(
        "/odoo/orm/run",
        json={"operation": "playbook", "name": "00-diagnostic-loop"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert "Diagnostic Loop" in response.json()["content"]


def test_playbook_operation_rejects_unknown_name():
    response = client.post(
        "/odoo/orm/run",
        json={"operation": "playbook", "name": "does-not-exist"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["error"] == "playbook_not_found"
    assert "records-missing" in detail["available"]


def test_playbook_operation_is_path_traversal_safe():
    for malicious in ("../SKILL", "../../connector", "/etc/passwd", "playbooks/records-missing"):
        response = client.post(
            "/odoo/orm/run",
            json={"operation": "playbook", "name": malicious},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 404


def test_raw_odoo_endpoint_requires_credentials_for_model_calls():
    response = client.post(
        "/odoo/orm/run",
        json={"model": "res.partner", "method": "search_count", "args": [[]]},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "odoo_credentials_required"


def test_execute_kw_authenticates_and_calls_through_jsonrpc():
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
    )

    with patch("app.core.odoo_client.httpx.Client", FakeClient):
        result = odoo.execute_kw(
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


def test_jsonrpc_retries_transient_gateway_errors():
    posted_payloads = []
    request = httpx.Request("POST", "https://example.odoo.com/jsonrpc")

    class GatewayResponse:
        status_code = 502
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "Server error '502 BAD GATEWAY'",
                request=request,
                response=httpx.Response(502, request=request),
            )

    class OkResponse:
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
            GatewayResponse(),
            OkResponse({"result": 7}),
            OkResponse({"result": [{"id": 1, "name": "EXCH-2026-03-0004"}]}),
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
        retry_backoff_seconds=0,
    )

    with patch("app.core.odoo_client.httpx.Client", FakeClient):
        result = odoo.execute_kw(
            "account.move",
            "search_read",
            args=[[["name", "=", "EXCH-2026-03-0004"]]],
            kwargs={"fields": ["id", "name"]},
        )

    assert result == [{"id": 1, "name": "EXCH-2026-03-0004"}]
    assert len(posted_payloads) == 3
    assert posted_payloads[0] == posted_payloads[1]
    assert posted_payloads[2]["params"]["method"] == "execute_kw"


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


def test_jsonrpc_error_prefers_data_message_when_debug_has_no_specific_cause():
    error = {
        "code": 200,
        "message": "Odoo Server Error",
        "data": {
            "name": "builtins.AttributeError",
            "message": "The method 'account.report.get_report_informations' does not exist",
            "debug": (
                "Traceback (most recent call last):\n"
                "  File \"/odoo/service/model.py\", line 39, in get_public_method\n"
                "    raise AttributeError(...)\n"
            ),
        },
    }

    message = compact_odoo_jsonrpc_error(error)

    assert message == "The method 'account.report.get_report_informations' does not exist"
    assert "Traceback" not in message


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_runs_single_model_method(mock_get_client):
    mock_client = MagicMock()
    mock_client.execute_kw.return_value = [{"id": 42, "name": "INV/001"}]
    mock_get_client.return_value = mock_client

    response = client.post(
        "/odoo/orm/run",
        json={
            "credentials": CREDENTIALS,
            "model": "account.move",
            "method": "search_read",
            "args": [[["name", "=", "INV/001"]]],
            "kwargs": {"fields": ["id", "name"], "limit": 1},
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == [{"id": 42, "name": "INV/001"}]
    mock_client.execute_kw.assert_called_once_with(
        "account.move",
        "search_read",
        args=[[["name", "=", "INV/001"]]],
        kwargs={"fields": ["id", "name"], "limit": 1},
    )


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_runs_batch(mock_get_client):
    mock_client = MagicMock()
    mock_client.execute_kw.side_effect = [
        [{"id": 10, "line_ids": [100]}],
        [{"id": 100, "balance": 250.0}],
    ]
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
                    "args": [[["name", "=", "INV/001"]]],
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
    assert mock_client.execute_kw.call_count == 2


@patch("app.routers.orm_runner._get_client")
def test_raw_odoo_endpoint_batch_errors_are_sanitized(mock_get_client):
    mock_client = MagicMock()
    mock_client.execute_kw.side_effect = RuntimeError("traceback with secret details")
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
                    "args": [[["name", "=", "INV/001"]]],
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
    mock_client.execute_kw.side_effect = OdooError(
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
