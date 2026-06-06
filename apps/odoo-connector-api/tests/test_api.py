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
    def test_query_marks_short_first_page_complete_without_count_call(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 1}, {"id": 2}]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="account.move",
                domain=[["write_date", ">=", "2026-06-04"]],
                limit=50,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["returned_count"] == 2
        assert data["total_count"] == 2
        assert data["has_more"] is False
        assert data["complete"] is True
        mock_client.search_count.assert_not_called()

    @patch("app.routers.ops_runner._get_client")
    def test_query_marks_full_page_incomplete_when_more_records_exist(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": i} for i in range(50)]
        mock_client.search_count.return_value = 72
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="account.move",
                domain=[["write_date", ">=", "2026-06-04"]],
                limit=50,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["returned_count"] == 50
        assert data["total_count"] == 72
        assert data["has_more"] is True
        assert data["complete"] is False
        mock_client.search_count.assert_called_once_with(
            model="account.move",
            domain=[["write_date", ">=", "2026-06-04"]],
        )

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
    def test_query_returns_concise_invalid_domain_field_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.side_effect = ValueError(
            "ValueError: Invalid field res.users.log.user_id in leaf ('user_id', '=', 782)"
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="res.users.log",
                domain=[["user_id", "=", 782]],
                fields=["create_date", "ip"],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()["detail"]
        assert data["error_type"] == "invalid_domain_field"
        assert data["model"] == "res.users.log"
        assert data["field"] == "user_id"
        assert "Traceback" not in data["message"]

    @patch("app.routers.ops_runner._get_client")
    def test_content_omits_invalid_fields_and_caps_unfiltered_reads(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "model": "res.users.log",
            "fields": {
                "name": {},
                "create_date": {},
                "write_date": {},
                "ip": {},
            },
            "field_errors": {
                "body": "Invalid field 'body' on model 'res.users.log'",
                "display_name": "Invalid field 'display_name' on model 'res.users.log'",
            },
        }
        mock_client.search_read.return_value = [{"id": i, "ip": "127.0.0.1"} for i in range(10)]
        mock_client.search_count.return_value = 50
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "content",
                model="res.users.log",
                content_fields=["ip", "body"],
                limit=50,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 10
        assert data["returned_count"] == 10
        assert data["total_count"] == 50
        assert data["has_more"] is True
        assert "body" in data["omitted_invalid_fields"]
        assert "display_name" in data["omitted_invalid_fields"]
        assert data["content_warnings"]
        search_kwargs = mock_client.search_read.call_args.kwargs
        assert search_kwargs["limit"] == 10
        assert search_kwargs["fields"] == ["id", "name", "create_date", "write_date", "ip"]

    @patch("app.routers.ops_runner._get_client")
    def test_content_returns_concise_invalid_domain_field_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "model": "res.users.log",
            "fields": {"create_date": {}, "ip": {}},
        }
        mock_client.search_read.side_effect = ValueError(
            "ValueError: Invalid field res.users.log.user_id in leaf ('user_id', '=', 782)"
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "content",
                model="res.users.log",
                domain=[["user_id", "=", 782]],
                content_fields=["ip"],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()["detail"]
        assert data["error_type"] == "invalid_domain_field"
        assert data["model"] == "res.users.log"
        assert data["field"] == "user_id"
        assert "Traceback" not in data["message"]

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

    @patch("app.routers.ops_runner._get_client")
    def test_execute_search_read_returns_pagination_metadata(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": i} for i in range(10)]
        mock_client.search_count.return_value = 10
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="mail.message",
                method="search_read",
                args=[[["date", ">=", "2026-06-04"]]],
                kwargs={"fields": ["id", "date"], "limit": 10},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["method"] == "search_read"
        assert data["returned_count"] == 10
        assert data["total_count"] == 10
        assert data["complete"] is True
        assert data["result"][0]["id"] == 0
        mock_client.search_read.assert_called_once_with(
            model="mail.message",
            domain=[["date", ">=", "2026-06-04"]],
            fields=["id", "date"],
            limit=10,
            offset=0,
            order=None,
            include_ids=True,
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
