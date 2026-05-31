import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from uuid import uuid4

from app.services.task_graph import TaskGraphExecutor, TaskNode
from app.services.model_router import execute_chat
from app.models.models import AIRoute, AIModel, AIProvider
from tests.test_model_router import MockSession


class TestTaskGraphExecutor:
    @pytest.mark.asyncio
    async def test_task_graph_not_implemented(self):
        executor = TaskGraphExecutor()
        
        results = await executor.execute_all("Compare credit note CN-12 to PDF attached.")
        
        # Returns a single not_implemented node
        assert len(results) == 1
        node = results[0]
        assert node["name"] == "Task Graph"
        assert node["execution_mode"] == "not_implemented"
        assert node["model_status"] == "inactive"
        assert node["cost_tier"] == "none"
        assert node["disabled_reason"] is not None
        assert "not yet implemented" in node["disabled_reason"]
        assert node["result"] is None

    @pytest.mark.asyncio
    async def test_task_graph_not_implemented_no_reconciliation_query(self):
        executor = TaskGraphExecutor()
        
        results = await executor.execute_all("Hello, how are you?")
        
        # Returns empty list for non-reconciliation queries
        assert len(results) == 0

    @pytest.mark.asyncio
    @patch("app.services.model_router.build_foundry_client")
    async def test_execute_chat_task_graph_not_implemented(self, mock_build_foundry_cls):
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

        async def mock_execute(stmt, *args, **kwargs):
            stmt_str = str(stmt).lower()
            res = MagicMock()
            if "ai_routes" in stmt_str:
                res.scalar_one_or_none = lambda: route
            elif "ai_models" in stmt_str:
                res.scalar_one_or_none = lambda: model
                res.first = lambda: None
            elif "ai_providers" in stmt_str:
                res.scalar_one_or_none = lambda: provider
            elif "ai_connected_accounts" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [], first=lambda: None)
            elif "ai_memories" in stmt_str:
                res.scalars = lambda: MagicMock(all=lambda: [])
            else:
                res.first = lambda: None
            return res

        db.execute = mock_execute
        db.add = MagicMock()
        db.flush = AsyncMock()

        mock_client = MagicMock()
        mock_chat_completion = AsyncMock(return_value={
            "error": False,
            "content": "Task graph not yet implemented.",
            "finish_reason": "stop",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "latency_ms": 100
        })
        mock_client.chat_completion = mock_chat_completion
        mock_build_foundry_cls.return_value = mock_client

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
             patch("app.services.model_router.get_enabled_route") as mock_get_enabled:
            
            result = await execute_chat(
                db, 
                [{"role": "user", "content": "Compare credit note CN-12 to PDF attached."}], 
                user_id=uuid4()
            )
            
            assert result["content"] == "Task graph not yet implemented."
            
            # Verify subtask metadata is present with not_implemented
            assert "subtasks" in result["context"]
            assert len(result["context"]["subtasks"]) == 1
            subtask = result["context"]["subtasks"][0]
            assert subtask["name"] == "Task Graph"
            assert subtask["execution_mode"] == "not_implemented"
            assert subtask["disabled_reason"] is not None
