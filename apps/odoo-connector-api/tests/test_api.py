import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.main import app

client = TestClient(app)

AUTH_HEADERS = {"X-Internal-API-Key": "test-internal-key"}
CREDENTIALS = {
    "url": "https://example.odoo.com",
    "db": "test",
    "username": "test",
    "api_key": "test",
}


def ops_payload(mode: str, **values):
    return {"credentials": CREDENTIALS, "mode": mode, **values}


class TestHealth:
    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["capabilities"] == ["odoo.ops.run"]

    def test_capabilities(self):
        response = client.get("/capabilities", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["endpoints"] == [
            {
                "path": "/odoo/ops/run",
                "method": "POST",
                "description": "Run consolidated Odoo operations by mode",
            }
        ]


class TestOdooOpsRunner:
    @patch("app.routers.ops_runner._get_client")
    def test_query_passes_db_unchanged(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = []
        mock_get_client.return_value = mock_client

        user_db = "aldenbronkhorst-lotslotsmore-lotslotsmore-15954717"
        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                credentials={**CREDENTIALS, "db": user_db},
                model="res.partner",
                domain=[],
                limit=1,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        creds_arg = mock_get_client.call_args[0][0]
        assert creds_arg.db == user_db

    @patch("app.routers.ops_runner._get_client")
    def test_query_retries_without_invalid_requested_fields(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.side_effect = [
            ValueError("Invalid field 'field_desc' on model 'mail.tracking.value'"),
            [{"id": 1, "old_value_char": "0", "new_value_char": "480"}],
        ]
        mock_client.fields_get.return_value = {
            "model": "mail.tracking.value",
            "fields": {
                "old_value_char": {},
                "new_value_char": {},
                "mail_message_id": {},
            },
            "partial": True,
            "field_errors": {
                "field_desc": "Invalid field 'field_desc' on model 'mail.tracking.value'",
            },
        }
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="mail.tracking.value",
                fields=["id", "field_desc", "old_value_char", "new_value_char", "mail_message_id"],
                domain=[],
                limit=5,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["omitted_invalid_fields"] == ["field_desc"]
        assert data["records"][0]["new_value_char"] == "480"
        assert mock_client.search_read.call_count == 2
        retry_kwargs = mock_client.search_read.call_args_list[1].kwargs
        assert retry_kwargs["fields"] == ["id", "old_value_char", "new_value_char", "mail_message_id"]

    @patch("app.routers.ops_runner._get_client")
    def test_execute_unlink_passes_through_to_odoo(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = True
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload("execute", model="res.partner", method="unlink", args=[[1]]),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        mock_client.call_with_transport.assert_called_once_with(
            "res.partner",
            "unlink",
            args=[[1]],
            kwargs={},
        )

    @patch("app.routers.ops_runner.OdooReportService")
    @patch("app.routers.ops_runner._get_client")
    def test_report_mode_uses_report_service(self, mock_get_client, mock_report_service):
        mock_get_client.return_value = MagicMock()
        mock_report_service.return_value.execute.return_value = {
            "report_name": "Profit and Loss",
            "line_count": 1,
        }

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "report",
                report_name="P&L",
                date_from="2026-05-01",
                date_to="2026-05-31",
                line_names=["Revenue"],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["report_name"] == "Profit and Loss"
