import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

os.environ.setdefault("ODOO_CONNECTOR_URL", "http://mock-connector:8000")
os.environ.setdefault("ODOO_CONNECTOR_API_KEY", "test-key")

from app.services.model_router import execute_chat
from app.models.models import AIRoute, AIModel, AIProvider, AIConnectedAccount, AITool
from tests.test_model_router import MockSession


class TestOdooExecuteReportRouter:
    @pytest.mark.asyncio
    @patch("app.services.model_router.build_foundry_client")
    async def test_execute_chat_calls_generic_report_tool(self, mock_build_foundry_cls):
        db = MockSession(has_config=False)
        
        route = AIRoute(
            id=uuid4(),
            task_type="general_chat",
            primary_model_id=uuid4(),
            enabled="true",
            temperature=0.3,
            max_tokens=2000,
            system_prompt="Standard Prompt"
        )
        model = AIModel(
            id=route.primary_model_id,
            provider_id=uuid4(),
            display_name="Kimi K2.6",
            model_name="Kimi-K2.6",
            deployment_name="kimi-k2-6",
            supports_tools="true",
            enabled="true"
        )
        provider = AIProvider(
            id=model.provider_id,
            name="Prov",
            provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com",
            enabled="true"
        )
        account = AIConnectedAccount(
            id=uuid4(),
            user_id=uuid4(),
            provider="odoo",
            provider_username="admin",
            odoo_url="https://test.odoo.com",
            odoo_db="test_db",
            secret_reference="mock-ref",
            status="active"
        )
        tool = AITool(
            id=uuid4(),
            name="odoo_ops_runner",
            display_name="Odoo Ops Runner",
            input_schema={}
        )

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                res.scalar_one_or_none = lambda: model
            elif "ai_providers" in stmt_str:
                res.scalar_one_or_none = lambda: provider
            elif "ai_connected_accounts" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [account], first=lambda: account)
            elif "ai_tools" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [tool])
            elif "ai_memories" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [])
            else:
                res.first = lambda: None
            return res

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()

        mock_client = MagicMock()
        mock_chat_completion = AsyncMock(side_effect=[
            {
                "error": False,
                "content": "",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {
                        "id": "call_pnl_123",
                        "type": "function",
                        "function": {
                            "name": "odoo_ops_runner",
                            "arguments": '{"mode": "report", "report_name": "Profit and Loss", "date_from": "2026-05-01", "date_to": "2026-05-31", "line_names": ["Revenue"]}'
                        }
                    }
                ],
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "latency_ms": 100
            },
            {
                "error": False,
                "content": "The Odoo P&L report lists total revenue of R 150,000.00 ZAR.",
                "finish_reason": "stop",
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "total_tokens": 280,
                "latency_ms": 150
            }
        ])
        mock_client.chat_completion = mock_chat_completion
        mock_build_foundry_cls.return_value = mock_client

        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.json = lambda: {
            "report_name": "Profit and Loss",
            "report_id": 123,
            "date_from": "2026-05-01",
            "date_to": "2026-05-31",
            "currency_code": "ZAR",
            "currency_symbol": "R",
            "source": "odoo_account_report",
            "line_count": 1,
            "available_line_names": ["Operating Revenue"],
            "missing_line_names": [],
            "lines": [
                {
                    "id": "rev_1",
                    "name": "Operating Revenue",
                    "code": "REV",
                    "level": 0,
                    "value": 150000.0,
                    "formatted_value": "R 150,000.00"
                }
            ]
        }

        mock_policy = {
            "selected_route_id": str(route.id),
            "selected_model_id": str(model.id),
            "fallback_model_id": None,
            "reason": "matched_request_task_type",
            "cost_tier": "medium",
            "quality_tier": "standard",
        }
        mock_select_route = AsyncMock(return_value=mock_policy)

        with patch("app.services.model_routing_policy.ModelRoutingPolicyService.select_route", new=mock_select_route), \
             patch("httpx.AsyncClient.post", return_value=mock_http_response), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mock_creds, \
             patch("app.services.model_router._resolve_api_key", return_value="secret-key"), \
             patch("app.services.model_router.get_enabled_route") as mock_get_enabled:
             
             mock_creds.return_value = {
                 "url": "https://test.odoo.com",
                 "db": "test_db",
                 "username": "admin",
                 "api_key": "secret-key",
                 "transport": "auto"
              }
             mock_get_enabled.return_value = (route, model, provider)
             
             result = await execute_chat(
                 db, 
                 [{"role": "user", "content": "What is revenue per P&L?"}], 
                 user_id=uuid4()
             )
             
             assert "R 150,000.00" in result["content"]
             assert result["prompt_tokens"] > 0


class TestStructuredToolErrors:
    """Tests for Fix 1: Preserve structured tool errors."""

    @pytest.mark.asyncio
    async def test_connector_error_json_preserved(self):
        from app.services.model_router import _execute_tool_call
        from unittest.mock import AsyncMock, patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error_type": "report_not_found",
            "message": "Report 'Profit and Loss' not found",
        }
        mock_resp.text = "raw fallback"
        with patch("httpx.AsyncClient.post", return_value=mock_resp), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mc, \
             patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), \
             patch("app.services.model_router.ODOO_CONNECTOR_KEY", "test-key"):
            mc.return_value = {"url": "https://test.odoo.com", "db": "test", "username": "admin", "api_key": "key"}
            result = await _execute_tool_call(AsyncMock(), uuid4(), "odoo_ops_runner",
                                              {"mode": "report", "report_name": "Profit and Loss"})
        assert result.get("error") is True
        assert result.get("status_code") == 400
        assert result.get("error_type") == "report_not_found"
        assert isinstance(result.get("connector_error"), dict)

    @pytest.mark.asyncio
    async def test_connector_error_fallback_on_non_json(self):
        from app.services.model_router import _execute_tool_call
        from unittest.mock import AsyncMock, patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.side_effect = ValueError("Not JSON")
        mock_resp.text = "Upstream connection refused"
        with patch("httpx.AsyncClient.post", return_value=mock_resp), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mc, \
             patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), \
             patch("app.services.model_router.ODOO_CONNECTOR_KEY", "test-key"):
            mc.return_value = {"url": "https://test.odoo.com", "db": "test", "username": "admin", "api_key": "key"}
            result = await _execute_tool_call(AsyncMock(), uuid4(), "odoo_ops_runner",
                                              {"mode": "report", "report_name": "Test"})
        assert result.get("error") is True
        assert result.get("error_type") == "connector_http_error"
        assert "connection refused" in result.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_connector_error_nested_detail_is_normalized(self):
        from app.services.model_router import _execute_tool_call
        from unittest.mock import AsyncMock, patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "detail": {
                "error_type": "invalid_domain_field",
                "message": "Field 'user_id' does not exist on Odoo model 'res.users.log'.",
                "model": "res.users.log",
                "field": "user_id",
            }
        }
        mock_resp.text = "raw fallback"
        with patch("httpx.AsyncClient.post", return_value=mock_resp), \
             patch("app.services.model_router._resolve_odoo_credentials_for_tool") as mc, \
             patch("app.services.model_router.ODOO_CONNECTOR_URL", "http://mock-connector:8000"), \
             patch("app.services.model_router.ODOO_CONNECTOR_KEY", "test-key"):
            mc.return_value = {"url": "https://test.odoo.com", "db": "test", "username": "admin", "api_key": "key"}
            result = await _execute_tool_call(AsyncMock(), uuid4(), "odoo_ops_runner",
                                              {"mode": "query", "model": "res.users.log"})
        assert result["error_type"] == "invalid_domain_field"
        assert result["connector_error"]["field"] == "user_id"
        assert "Traceback" not in result["message"]


class TestReportFallbackAnswer:
    """Tests for Fix 2/5: Fallback answer builder."""

    def test_fallback_from_report_lines(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "report_name": "Profit and Loss",
                "date_from": "2026-06-01", "date_to": "2026-06-30",
                "currency_code": "ZAR", "currency_symbol": "R",
                "lines": [
                    {"name": "Revenue", "value": 150000.0, "formatted_value": "150,000.00"},
                ],
            }},
        ])
        assert result is not None
        assert "Profit and Loss" in result
        assert "2026-06-01" in result
        assert "R" in result
        assert "Revenue" in result

    def test_fallback_without_matching_lines(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "report_name": "Profit and Loss",
                "lines": [],
                "available_line_names": ["Revenue", "Expenses", "Net Income"],
            }},
        ])
        assert result is not None
        assert "Revenue" in result and "Expenses" in result

    def test_fallback_from_tool_error(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True, "error_type": "report_not_found",
                "message": "Report not found",
            }},
        ])
        assert result is not None
        assert "report" in result.lower() and "could not find" in result.lower()

    def test_fallback_ignores_non_report_tools(self):
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "azure_cli", "result": {"stdout": ""}},
        ])
        assert result is None


class TestReportDiscovery:
    """Tests for consolidated report routing."""

    def test_only_odoo_ops_runner_is_mapped(self):
        from app.services.model_router import _map_odoo_tool_to_path
        assert _map_odoo_tool_to_path("odoo_ops_runner") == "/odoo/ops/run"
        assert _map_odoo_tool_to_path("odoo_list_reports") == ""
        assert _map_odoo_tool_to_path("odoo_get_profit_and_loss") == ""


class TestCleanFallback:
    """Tests for Fix 4: Clean user-facing fallback errors."""

    def test_fallback_no_raw_dicts(self):
        """Fallback answer must never contain raw dict repr."""
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True,
                "connector_error": {
                    "detail": {
                        "error": "report_unavailable",
                        "message": "Could not execute Odoo account report 'Profit and Loss'.",
                    },
                },
            }},
        ])
        assert result is not None
        # Must not contain raw Python dict formatting
        assert "{'detail'" not in result
        assert "{" not in result
        assert "odoo" in result.lower()
        assert "could not execute" in result.lower()

    def test_fallback_technical_error_clean(self):
        """Technical error with 'id' must produce clean message."""
        from app.services.model_router import _build_report_fallback_answer
        result = _build_report_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
                "error": True,
                "connector_error": {
                    "detail": {
                        "error": "report_unavailable",
                        "message": "Could not execute Odoo account report 'Profit and Loss'. Technical error: 'id'",
                    },
                },
            }},
        ])
        assert result is not None
        assert "{'detail'" not in result
        assert "Technical error" in result
        assert "'id'" in result


class TestExecuteChatReportFallback:
    """Tests for fallback answer in execute_chat before Reviewer."""

    @pytest.mark.asyncio
    async def test_execute_chat_fallback_on_blank_content(self):
        """Blank model content + successful report tool must produce fallback answer."""
        from app.services.model_router import execute_chat, _build_report_fallback_answer
        tool_results = [{"tool_name": "odoo_ops_runner", "arguments": {"mode": "report"}, "result": {
            "report_name": "Profit and Loss",
            "date_from": "2026-06-01", "date_to": "2026-06-30",
            "currency_code": "ZAR", "currency_symbol": "R",
            "lines": [{"name": "Revenue", "value": 150000.0, "formatted_value": "150,000.00"}],
        }}]
        fallback = _build_report_fallback_answer(tool_results)
        assert fallback is not None
        assert "Profit and Loss" in fallback
        assert "150,000" in fallback
        assert "R" in fallback

    def test_fallback_returns_none_when_no_report_tool(self):
        """Non-report tools must not produce a fallback."""
        from app.services.model_router import _build_report_fallback_answer
        fallback = _build_report_fallback_answer([
            {"tool_name": "azure_cli", "result": {"stdout": ""}},
        ])
        assert fallback is None

    def test_odoo_evidence_fallback_builds_timeline(self):
        from app.services.model_router import _build_odoo_evidence_fallback_answer
        fallback = _build_odoo_evidence_fallback_answer([
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "query", "model": "mail.message"}, "result": {
                "model": "mail.message",
                "records": [
                    {
                        "id": 2,
                        "create_date": "2026-06-05 09:23:31",
                        "model": "account.move",
                        "res_id": 57912,
                        "body": "<p>Vendor Bill Created</p>",
                        "message_type": "notification",
                    },
                    {
                        "id": 1,
                        "create_date": "2026-06-05 07:54:41",
                        "model": "account.move",
                        "res_id": 33396,
                        "body": "",
                        "message_type": "notification",
                    },
                ],
                "count": 2,
                "returned_count": 2,
                "total_count": 2,
            }},
            {"tool_name": "odoo_ops_runner", "arguments": {"mode": "query", "model": "res.users.log"}, "result": {
                "error": True,
                "error_type": "invalid_domain_field",
                "message": "Field 'user_id' does not exist on Odoo model 'res.users.log'.",
            }},
        ])

        assert fallback is not None
        assert "2026-06-05 07:54:41" in fallback
        assert "2026-06-05 09:23:31" in fallback
        assert "Vendor Bill Created" in fallback
        assert "<p>" not in fallback
        assert "invalid_domain_field" in fallback

    def test_pnl_uses_generic_report_tool(self):
        """P&L question must route through odoo_ops_runner, not a dedicated tool."""
        from app.services.model_router import _map_odoo_tool_to_path
        path = _map_odoo_tool_to_path("odoo_ops_runner")
        assert path == "/odoo/ops/run"
        # No dedicated P&L tool should exist
        assert _map_odoo_tool_to_path("odoo_get_profit_and_loss") == ""
        assert _map_odoo_tool_to_path("get_revenue_this_month") == ""
