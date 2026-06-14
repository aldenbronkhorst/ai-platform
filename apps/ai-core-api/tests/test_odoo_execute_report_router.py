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
    @patch("app.services.model_router.build_model_client")
    async def test_execute_chat_calls_generic_report_tool(self, mock_build_model_client):
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
            display_name="Provider Chat",
            model_name="provider-chat-latest",
            deployment_name="provider-chat-latest",
            supports_tools="true",
            enabled="true"
        )
        provider = AIProvider(
            id=model.provider_id,
            name="ProviderOne",
            provider_type="openai_compatible",
            base_url="https://provider-one.example/v1",
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
        mock_build_model_client.return_value = mock_client

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

        with patch("httpx.AsyncClient.post", return_value=mock_http_response), \
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


class TestReportDiscovery:
    """Tests for consolidated report routing."""

    def test_only_odoo_ops_runner_is_canonical(self):
        from app.services.model_tool_calls import _canonical_tool_invocation

        assert _canonical_tool_invocation("odoo_ops_runner", {"mode": "report"}) == (
            "odoo_ops_runner",
            {"mode": "report"},
        )
        assert _canonical_tool_invocation("odoo_get_profit_and_loss", {}) == (
            "odoo_get_profit_and_loss",
            {},
        )
