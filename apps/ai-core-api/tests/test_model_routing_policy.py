import pytest
from uuid import uuid4, UUID
from unittest.mock import patch, MagicMock, AsyncMock

from app.models.models import AIModel, AIRoute, AIProvider
from app.services.model_routing_policy import ModelRoutingPolicyService
from app.services.model_router import execute_chat
from tests.test_model_router import MockSession


class TestModelRoutingPolicy:
    @pytest.mark.asyncio
    async def test_routing_policy_selects_general_chat(self):
        db = MockSession(has_config=False)

        route = AIRoute(
            id=uuid4(),
            task_type="general_chat",
            primary_model_id=uuid4(),
            fallback_model_id=None,
            enabled="true"
        )
        model = AIModel(
            id=route.primary_model_id,
            provider_id=uuid4(),
            display_name="Cheap Chat Model",
            model_name="cheap-chat",
            deployment_name="cheap-chat-deployment",
            supports_tools="true",
            enabled="true",
            config_json={"cost_tier": "low", "quality_tier": "basic"}
        )

        class QueryResult:
            def scalar_one_or_none(self):
                # We return route or model depending on what's queried
                # Since the execution sequence is route then model, we can return them based on model type
                return None

        # Custom execute dispatcher to return the right models
        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                res.scalar_one_or_none = lambda: model
            return res

        db.execute = mock_execute

        svc = ModelRoutingPolicyService(db)
        policy = await svc.select_route(task_type="general_chat", risk_level="low")

        assert policy["selected_route_id"] == str(route.id)
        assert policy["selected_model_id"] == str(model.id)
        assert policy["cost_tier"] == "low"
        assert policy["reason"] == "matched_request_task_type"

    @pytest.mark.asyncio
    async def test_routing_policy_finance_escalation(self):
        db = MockSession(has_config=False)

        route = AIRoute(
            id=uuid4(),
            task_type="finance",
            primary_model_id=uuid4(),
            fallback_model_id=None,
            enabled="true"
        )
        model = AIModel(
            id=route.primary_model_id,
            provider_id=uuid4(),
            display_name="Advanced Financial Model",
            model_name="finance-model",
            deployment_name="finance-deployment",
            supports_tools="true",
            enabled="true",
            config_json={"cost_tier": "high", "quality_tier": "advanced"}
        )

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                res.scalar_one_or_none = lambda: model
            return res

        db.execute = mock_execute

        svc = ModelRoutingPolicyService(db)
        # Esclate general_chat to finance because risk_level is high
        policy = await svc.select_route(task_type="general_chat", risk_level="high")

        assert policy["selected_route_id"] == str(route.id)
        assert policy["selected_model_id"] == str(model.id)
        assert policy["cost_tier"] == "high"
        assert policy["reason"] == "high_risk_escalation_to_finance_route"

    @pytest.mark.asyncio
    @patch("app.services.model_router.build_foundry_client")
    async def test_execute_chat_fallback_success(self, mock_build_foundry_cls):
        db = MockSession(has_config=False)

        # Setup route, model, and fallback model
        fallback_model_id = uuid4()
        route = AIRoute(
            id=uuid4(),
            task_type="general_chat",
            primary_model_id=uuid4(),
            fallback_model_id=fallback_model_id,
            enabled="true",
            temperature=0.3,
            max_tokens=2000,
            system_prompt="Standard Prompt"
        )
        primary_model = AIModel(
            id=route.primary_model_id,
            provider_id=uuid4(),
            display_name="Primary Model",
            model_name="primary",
            deployment_name="primary-deploy",
            supports_tools="true",
            enabled="true"
        )
        fallback_model = AIModel(
            id=fallback_model_id,
            provider_id=uuid4(),
            display_name="Fallback Model",
            model_name="fallback",
            deployment_name="fallback-deploy",
            supports_tools="true",
            enabled="true"
        )
        provider = AIProvider(
            id=primary_model.provider_id,
            name="Primary Prov",
            provider_type="azure_foundry",
            base_url="https://mock.services.ai.azure.com",
            enabled="true"
        )

        model_calls = 0
        async def mock_execute(stmt, *args, **kwargs):
            nonlocal model_calls
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                model_calls += 1
                if model_calls == 1:
                    res.scalar_one_or_none = lambda: primary_model
                else:
                    res.scalar_one_or_none = lambda: fallback_model
            elif "ai_providers" in stmt_str:
                res.scalar_one_or_none = lambda: provider
            elif "ai_connected_accounts" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [], first=lambda: None)
            elif "ai_memories" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [])
            return res

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()

        # Mock ModelRoutingPolicyService.select_route
        mock_policy = {
            "selected_route_id": str(route.id),
            "selected_model_id": str(primary_model.id),
            "fallback_model_id": str(fallback_model_id),
            "reason": "finance_high_risk_requires_tools",
            "cost_tier": "high",
            "quality_tier": "advanced",
        }
        mock_select_route = AsyncMock(return_value=mock_policy)

        # Mock Kimi primary client throwing 429 quota error, and fallback client succeeding
        primary_client = MagicMock()
        primary_client.chat_completion = AsyncMock(return_value={
            "error": True,
            "error_type": "quota_exceeded",
            "message": "Quota limit reached",
            "status_code": 429,
            "latency_ms": 50
        })

        fallback_client = MagicMock()
        fallback_client.chat_completion = AsyncMock(return_value={
            "error": False,
            "content": "Succeeded via fallback",
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 100
        })

        def mock_build_client(_prov, model_obj):
            if model_obj.id == primary_model.id:
                return primary_client
            return fallback_client

        mock_build_foundry_cls.side_effect = mock_build_client

        with patch("app.services.model_router.get_enabled_route") as mock_get_enabled, \
             patch("app.services.model_routing_policy.ModelRoutingPolicyService.select_route", new=mock_select_route):
            result = await execute_chat(db, [{"role": "user", "content": "hi"}], user_id=uuid4())
            assert result["content"] == "Succeeded via fallback"
            assert result["context"]["model_routing"]["fallback_used"] is True
            assert result["context"]["model_routing"]["primary_model"] == "Primary Model"
            assert result["context"]["model_routing"]["fallback_model"] == "Fallback Model"
            assert result["context"]["model_routing"]["routing_reason"] == "finance_high_risk_requires_tools"
            assert result["context"]["model_routing"]["cost_tier"] == "high"
