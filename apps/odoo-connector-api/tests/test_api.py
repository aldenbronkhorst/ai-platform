import os
import xmlrpc.client
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "true"
os.environ["INTERNAL_API_KEY"] = "test-internal-key"

from app.main import app
from app.core.odoo_client import OdooError

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
    def test_schema_handles_unavailable_candidate_model(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.side_effect = OdooError(
            "Both Odoo API transports failed. JSON-RPC: Traceback ...; XML-RPC: Traceback ..."
        )
        mock_client.search_read.return_value = []
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload("schema", model="auditlog.log"),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["error"] is True
        assert data["handled"] is True
        assert data["status"] == "skipped"
        assert data["error_type"] == "model_unavailable"
        assert data["model"] == "auditlog.log"
        assert data["fields"] == {}
        assert data["model_exists"] is False
        assert "Traceback" not in data["reason"]
        mock_client.search_read.assert_called_once_with(
            model="ir.model",
            domain=[["model", "=", "auditlog.log"]],
            fields=["model", "name"],
            limit=1,
            include_ids=True,
        )

    @patch("app.routers.ops_runner._get_client")
    def test_schema_handles_existing_model_that_cannot_be_inspected(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.side_effect = OdooError("Access denied while reading model fields")
        mock_client.search_read.return_value = [{"id": 10, "model": "auditlog.log", "name": "Audit Log"}]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload("schema", model="auditlog.log"),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["error"] is True
        assert data["handled"] is True
        assert data["status"] == "skipped"
        assert data["error_type"] == "schema_unavailable"
        assert data["model_exists"] is True
        assert "could not be inspected" in data["message"]

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
        assert data["records"][0]["record_url"] == "https://example.odoo.com/web#id=1&model=account.move&view_type=form"
        assert data["records"][1]["record_url"] == "https://example.odoo.com/web#id=2&model=account.move&view_type=form"
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
    def test_query_normalizes_mail_message_res_model_alias(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = [
            {"id": 10, "model": "purchase.order", "res_id": 55},
            {"id": 11, "model": "purchase.order", "res_id": 56},
        ]
        mock_client.search_count.return_value = 2
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="mail.message",
                domain=[["res_model", "=", "purchase.order"], ["res_id", "in", [55, 56]]],
                fields=["id", "res_model", "res_id"],
                limit=2,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["records"][0]["model"] == "purchase.order"
        assert data["total_count"] == 2
        expected_domain = [["model", "=", "purchase.order"], ["res_id", "in", [55, 56]]]
        mock_client.search_read.assert_called_once_with(
            model="mail.message",
            domain=expected_domain,
            fields=["id", "model", "res_id"],
            limit=2,
            offset=0,
            order=None,
            include_ids=True,
        )
        mock_client.search_count.assert_called_once_with(model="mail.message", domain=expected_domain)

    @patch("app.routers.ops_runner._get_client")
    def test_query_normalizes_mixed_implicit_and_or_domain(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 23591}]
        mock_get_client.return_value = mock_client

        mixed_domain = [
            ["user_id", "=", 782],
            "|",
            [
                "&",
                ["first_activity", ">=", "2026-06-05 22:00:00"],
                ["first_activity", "<", "2026-06-06 22:00:00"],
            ],
            [
                "&",
                ["last_activity", ">=", "2026-06-05 22:00:00"],
                ["last_activity", "<", "2026-06-06 22:00:00"],
            ],
        ]

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "query",
                model="res.device",
                domain=mixed_domain,
                fields=["id", "first_activity", "last_activity"],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["count"] == 1
        search_kwargs = mock_client.search_read.call_args.kwargs
        assert search_kwargs["domain"] == [
            "&",
            ["user_id", "=", 782],
            "|",
            [
                "&",
                ["first_activity", ">=", "2026-06-05 22:00:00"],
                ["first_activity", "<", "2026-06-06 22:00:00"],
            ],
            [
                "&",
                ["last_activity", ">=", "2026-06-05 22:00:00"],
                ["last_activity", "<", "2026-06-06 22:00:00"],
            ],
        ]

    @patch("app.routers.ops_runner._get_client")
    def test_aggregate_allows_empty_groupby_for_global_totals(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = [{"amount_total_signed": 939677.63, "__count": 26}]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "aggregate",
                model="account.move",
                domain=[
                    ["state", "=", "posted"],
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                    ["date", ">=", "2026-06-01"],
                    ["date", "<=", "2026-06-08"],
                ],
                fields=["amount_total_signed:sum"],
                groupby=[],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["groupby"] == []
        assert data["groups"][0]["amount_total_signed"] == 939677.63
        mock_client.call_with_transport.assert_called_once_with(
            "account.move",
            "read_group",
            args=[
                [
                    ["state", "=", "posted"],
                    ["move_type", "in", ["out_invoice", "out_refund"]],
                    ["date", ">=", "2026-06-01"],
                    ["date", "<=", "2026-06-08"],
                ],
                ["amount_total_signed:sum"],
                [],
            ],
            kwargs={"lazy": True},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_aggregate_missing_fields_returns_handled_skip(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "aggregate",
                model="account.move",
                domain=[["state", "=", "posted"]],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["error"] is True
        assert data["handled"] is True
        assert data["status"] == "skipped"
        assert data["error_type"] == "aggregate_arguments_required"
        assert data["missing"] == ["fields"]
        mock_client.call_with_transport.assert_not_called()

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
    def test_content_ids_constrain_search_domain(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "model": "res.users.log",
            "fields": {
                "name": {},
                "create_date": {},
                "write_date": {},
                "ip": {},
            },
        }
        mock_client.search_read.return_value = [{"id": 123, "ip": "127.0.0.1"}]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "content",
                model="res.users.log",
                ids=[123],
                content_fields=["ip"],
                limit=50,
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["limit"] == 50
        assert data["returned_count"] == 1
        assert "content_warnings" not in data
        search_kwargs = mock_client.search_read.call_args.kwargs
        assert search_kwargs["domain"] == [["id", "in", [123]]]
        assert search_kwargs["limit"] == 50

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
    def test_message_mode_wraps_record_id_for_message_post(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = 9001
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "message",
                model="purchase.order",
                record_id=23337,
                operation="post",
                body="Fixed & ready\nProceed",
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["operation"] == "post"
        assert data["result"] == 9001
        assert data["message_id"] == 9001
        assert data["record_url"] == "https://example.odoo.com/web#id=23337&model=purchase.order&view_type=form"
        mock_client.call_with_transport.assert_called_once_with(
            "purchase.order",
            "message_post",
            args=[[23337]],
            kwargs={"body": "Fixed &amp; ready<br/>Proceed", "message_type": "comment"},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_message_mode_defaults_missing_operation_to_post(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = 9002
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "message",
                model="res.partner",
                record_id=42,
                body="Fixed the PO; you can bill now.",
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["operation"] == "post"
        assert data["result"] == 9002
        assert data["message_id"] == 9002
        assert data["record_url"] == "https://example.odoo.com/web#id=42&model=res.partner&view_type=form"
        mock_client.call_with_transport.assert_called_once_with(
            "res.partner",
            "message_post",
            args=[[42]],
            kwargs={"body": "Fixed the PO; you can bill now.", "message_type": "comment"},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_record_url_strips_web_path_from_configured_url(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = 9003
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "message",
                credentials={**CREDENTIALS, "url": "https://example.odoo.com/web"},
                model="purchase.order",
                record_id=23337,
                body="Fixed",
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["record_url"] == "https://example.odoo.com/web#id=23337&model=purchase.order&view_type=form"

    @patch("app.routers.ops_runner._get_client")
    def test_execute_message_post_uses_record_id_when_args_missing(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = 9002
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="purchase.order",
                method="message_post",
                record_id=23337,
                kwargs={"body": "Fixed"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message_id"] == 9002
        assert data["record_url"] == "https://example.odoo.com/web#id=23337&model=purchase.order&view_type=form"
        assert data["record_urls"] == [
            {"id": 23337, "url": "https://example.odoo.com/web#id=23337&model=purchase.order&view_type=form"}
        ]
        mock_client.call_with_transport.assert_called_once_with(
            "purchase.order",
            "message_post",
            args=[[23337]],
            kwargs={"body": "Fixed"},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_execute_action_feedback_uses_ids_when_args_missing(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = True
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="mail.activity",
                method="action_feedback",
                ids=[2180],
                kwargs={"feedback": "Receipt corrected"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        mock_client.call_with_transport.assert_called_once_with(
            "mail.activity",
            "action_feedback",
            args=[[2180]],
            kwargs={"feedback": "Receipt corrected"},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_execute_recordset_method_missing_ids_returns_400(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="mail.activity",
                method="action_feedback",
                kwargs={"feedback": "Receipt corrected"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()["detail"]
        assert data["error_type"] == "record_ids_required"
        assert data["missing"] == ["ids", "record_id", "args[0]"]
        mock_client.call_with_transport.assert_not_called()

    @patch("app.routers.ops_runner._get_client")
    def test_execute_recordset_method_normalizes_bare_int_first_arg(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = True
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="mail.activity",
                method="action_feedback",
                args=[2180],
                kwargs={"feedback": "Receipt corrected"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        mock_client.call_with_transport.assert_called_once_with(
            "mail.activity",
            "action_feedback",
            args=[[2180]],
            kwargs={"feedback": "Receipt corrected"},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_execute_recordset_method_prepends_ids_to_non_id_args(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.return_value = True
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "execute",
                model="res.partner",
                method="write",
                ids=[42],
                args=[{"name": "Updated"}],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        mock_client.call_with_transport.assert_called_once_with(
            "res.partner",
            "write",
            args=[[42], {"name": "Updated"}],
            kwargs={},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_write_normalizes_bare_x2many_set_command(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "model": "hr.employee",
            "fields": {
                "contract_ids": {"type": "one2many", "relation": "hr.contract"},
                "work_email": {"type": "char"},
            },
        }
        mock_client.call_with_transport.return_value = True
        mock_client.read.return_value = [{"id": 76, "display_name": "Gerhard Wayne Cloete"}]
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "mutation",
                operation="write",
                model="hr.employee",
                ids=[76],
                values={"contract_ids": [6, 0, [12]], "work_email": "gerhard@example.com"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        mock_client.call_with_transport.assert_called_once_with(
            "hr.employee",
            "write",
            args=[[76], {"contract_ids": [[6, 0, [12]]], "work_email": "gerhard@example.com"}],
            kwargs={},
        )

    @patch("app.routers.ops_runner._get_client")
    def test_write_rejects_invalid_x2many_shape_before_odoo(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "model": "hr.employee",
            "fields": {"contract_ids": {"type": "one2many", "relation": "hr.contract"}},
        }
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "mutation",
                operation="write",
                model="hr.employee",
                ids=[76],
                values={"contract_ids": [{"id": 12}]},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()["detail"]
        assert data["error_type"] == "invalid_x2many_value"
        assert data["field"] == "contract_ids"
        mock_client.call_with_transport.assert_not_called()

    @patch("app.routers.ops_runner._get_client")
    def test_raw_xmlrpc_fault_returns_structured_odoo_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.side_effect = xmlrpc.client.Fault(
            1,
            "Traceback (most recent call last):\n"
            "psycopg2.errors.UndefinedFunction: operator does not exist: integer <> integer[]\n",
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "mutation",
                operation="write",
                model="hr.employee",
                ids=[76],
                values={"work_email": "gerhard@example.com"},
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_type"] == "odoo_rpc_fault"
        assert "operator does not exist" in data["message"]
        assert "Traceback" not in data["message"]

    @patch("app.routers.ops_runner._get_client")
    def test_delete_blocked_by_active_pos_session_is_classified(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.side_effect = OdooError(
            "Odoo hr.employee.unlink failed: You cannot delete an employee that may be used "
            "in an active PoS session, close the session(s) first: "
            "Employee: Gerhard Wayne Cloete - PoS Config(s): Gallagher Convention Center"
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "mutation",
                operation="delete",
                model="hr.employee",
                ids=[77],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_type"] == "odoo_delete_blocked_active_pos_session"
        assert "active PoS session" in data["message"]
        assert "Gallagher Convention Center" in data["message"]

    @patch("app.routers.ops_runner._get_client")
    def test_delete_blocked_does_not_treat_possible_as_pos(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.call_with_transport.side_effect = OdooError(
            "You cannot delete this record. Archive it if possible because another record still references it."
        )
        mock_get_client.return_value = mock_client

        response = client.post(
            "/odoo/ops/run",
            json=ops_payload(
                "mutation",
                operation="delete",
                model="hr.employee",
                ids=[77],
            ),
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error_type"] == "odoo_delete_blocked"
        assert data["error_type"] != "odoo_delete_blocked_active_pos_session"

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
