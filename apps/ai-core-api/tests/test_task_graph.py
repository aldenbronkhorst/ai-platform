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
    async def test_task_graph_structure_and_parallel_execution(self):
        executor = TaskGraphExecutor()
        
        # Test executing a complex credit note compare query
        results = await executor.execute_all("Compare credit note CN-12 to PDF attached.")
        
        # Verify 3 subtasks were defined and executed
        assert len(results) == 3
        
        odoo_task = [t for t in results if t["name"] == "Odoo Data Worker"][0]
        pdf_task = [t for t in results if t["name"] == "PDF Extraction Worker"][0]
        reconcile_task = [t for t in results if t["name"] == "Reconciliation Worker"][0]
        
        assert odoo_task["status"] == "complete"
        assert odoo_task["model"] == "none"
        assert odoo_task["execution_mode"] == "deterministic"
        assert odoo_task["model_status"] == "inactive"
        assert odoo_task["cost_tier"] == "none"
        assert odoo_task["result"]["credit_note_number"] == "CN-2026-0012"
        
        assert pdf_task["status"] == "complete"
        assert pdf_task["model"] == "none"
        assert pdf_task["planned_model"] == "DeepSeek Flash"
        assert pdf_task["execution_mode"] == "deterministic"
        assert pdf_task["model_status"] == "inactive"
        assert pdf_task["cost_tier"] == "none"
        assert pdf_task["result"]["pdf_filename"] == "credit_note_reconcile.pdf"
        assert "provider" not in pdf_task
        assert "deployment" not in pdf_task
        assert "token_usage" not in pdf_task
        
        assert reconcile_task["status"] == "complete"
        assert reconcile_task["model"] == "none"
        assert reconcile_task["planned_model"] == "Qwen Max"
        assert reconcile_task["execution_mode"] == "deterministic"
        assert reconcile_task["model_status"] == "inactive"
        assert reconcile_task["cost_tier"] == "none"
        assert reconcile_task["disabled_reason"] == "DashScope provider integration required"
        assert len(reconcile_task["result"]["discrepancies"]) == 1
        assert "provider" not in reconcile_task
        assert "deployment" not in reconcile_task
        assert "token_usage" not in reconcile_task

    @pytest.mark.asyncio
    async def test_task_graph_with_active_models(self):
        # Setup a mock DB and models
        db = AsyncMock()
        
        ds_model = AIModel(
            id=uuid4(),
            provider_id=uuid4(),
            display_name="DeepSeek Flash",
            model_name="DeepSeek-V4-Flash",
            deployment_name="deepseek-v4-flash",
            enabled="true"
        )
        ds_prov = AIProvider(
            id=ds_model.provider_id,
            name="Microsoft Foundry",
            enabled="true"
        )
        
        qw_model = AIModel(
            id=uuid4(),
            provider_id=uuid4(),
            display_name="Qwen 2.5",
            model_name="Qwen2.5-72B-Instruct",
            deployment_name="qwen-2-5-72b-instruct-general-chat",
            enabled="true"
        )
        qw_prov = AIProvider(
            id=qw_model.provider_id,
            name="Microsoft Foundry",
            enabled="true"
        )

        async def mock_execute(stmt, *args, **kwargs):
            try:
                params = stmt.compile().params
            except Exception:
                params = {}
            res = MagicMock()
            # Find if any parameter value matches the model names
            is_deepseek = any(val == "DeepSeek-V4-Flash" for val in params.values())
            is_qwen = any(val == "Qwen2.5-72B-Instruct" for val in params.values())
            
            if is_deepseek:
                res.first = lambda: (ds_model, ds_prov)
            elif is_qwen:
                res.first = lambda: (qw_model, qw_prov)
            else:
                res.first = lambda: None
            return res

        db.execute = mock_execute

        executor = TaskGraphExecutor()
        results = await executor.execute_all("Compare credit note CN-12 to PDF attached.", db=db)

        assert len(results) == 3
        
        odoo_task = [t for t in results if t["name"] == "Odoo Data Worker"][0]
        pdf_task = [t for t in results if t["name"] == "PDF Extraction Worker"][0]
        reconcile_task = [t for t in results if t["name"] == "Reconciliation Worker"][0]

        # Odoo Data Worker should still be deterministic
        assert odoo_task["execution_mode"] == "deterministic"
        assert odoo_task["model"] == "none"

        # PDF Extraction Worker should be model/active
        assert pdf_task["execution_mode"] == "model"
        assert pdf_task["model"] == "DeepSeek Flash"
        assert pdf_task["planned_model"] == "DeepSeek Flash"
        assert pdf_task["model_status"] == "active"
        assert pdf_task["cost_tier"] == "low"
        assert pdf_task["provider"] == "Microsoft Foundry"
        assert pdf_task["deployment"] == "deepseek-v4-flash"
        assert pdf_task["token_usage"] == {"prompt_tokens": 150, "completion_tokens": 80}

        # Reconciliation Worker should be model/active
        assert reconcile_task["execution_mode"] == "model"
        assert reconcile_task["model"] == "Qwen"
        assert reconcile_task["planned_model"] == "Qwen Max"
        assert reconcile_task["model_status"] == "active"
        assert reconcile_task["cost_tier"] == "medium"
        assert reconcile_task["provider"] == "Microsoft Foundry"
        assert reconcile_task["deployment"] == "qwen-2-5-72b-instruct-general-chat"
        assert reconcile_task["token_usage"] == {"prompt_tokens": 450, "completion_tokens": 200}

    @pytest.mark.asyncio
    @patch("app.services.model_router.build_foundry_client")
    async def test_execute_chat_reconciliation_triggers_task_graph(self, mock_build_foundry_cls):
        db = MockSession(has_config=False)

        # Setup route, model, and provider
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

        # Mock the chat client response
        mock_client = MagicMock()
        mock_chat_completion = AsyncMock(return_value={
            "error": False,
            "content": "Reconciliation complete, discrepancies found in consulting fees.",
            "finish_reason": "stop",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "latency_ms": 100
        })
        mock_client.chat_completion = mock_chat_completion
        mock_build_foundry_cls.return_value = mock_client

        # Mock ModelRoutingPolicyService.select_route to return our static general_chat route
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
            
            assert result["content"] == "Reconciliation complete, discrepancies found in consulting fees."
            
            # Verify system prompt has subtask injection
            called_messages = mock_chat_completion.call_args[1]["messages"]
            system_prompt_content = called_messages[0]["content"]
            assert "## Ephemeral Sub-Agent / Task Worker Results" in system_prompt_content
            assert "Subtask 'Odoo Data Worker' (complete)" in system_prompt_content
            assert "Subtask 'PDF Extraction Worker' (complete)" in system_prompt_content
            assert "Subtask 'Reconciliation Worker' (complete)" in system_prompt_content

            # Verify response contains subtask metadata
            assert "subtasks" in result["context"]
            assert len(result["context"]["subtasks"]) == 3
            assert result["context"]["subtasks"][0]["name"] == "Odoo Data Worker"
            assert result["context"]["subtasks"][1]["name"] == "PDF Extraction Worker"
            assert result["context"]["subtasks"][2]["name"] == "Reconciliation Worker"
