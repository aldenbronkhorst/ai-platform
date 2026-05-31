import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from app.services.reviewer import ReviewerAgent
from app.schemas.schemas import ReviewRequest
from app.services.model_router import execute_chat
from app.models.models import AIRoute, AIModel, AIProvider, AIConnectedAccount, AITool
from tests.test_model_router import MockSession


class TestOdooReportsRouter:
    @pytest.mark.asyncio
    async def test_reviewer_approved_with_currency(self):
        agent = ReviewerAgent()
        
        req = ReviewRequest(
            user_question="What is the revenue?",
            content="According to the Odoo P&L report, the revenue is R 123,456.78 ZAR for the selected period.",
            tool_results=[]
        )
        result = await agent.review(req)
        assert result.approved is True
        assert result.risk_level == "high"

    @pytest.mark.asyncio
    async def test_reviewer_rejected_missing_currency(self):
        agent = ReviewerAgent()
        
        req = ReviewRequest(
            user_question="What is the revenue?",
            content="According to the Odoo P&L report, the revenue is 123456.78 for the selected period.",
            tool_results=[]
        )
        result = await agent.review(req)
        assert result.approved is False
        assert "currency symbol" in result.reviewer_notes.lower()

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
            name="odoo_execute_report",
            display_name="Odoo Accounting Report",
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
                            "name": "odoo_execute_report",
                            "arguments": '{"report_name": "Profit and Loss", "date_from": "2026-05-01", "date_to": "2026-05-31", "line_names": ["Revenue"]}'
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
