import logging
import asyncio
import time
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import AIModel, AIProvider

logger = logging.getLogger(__name__)


class TaskNode:
    def __init__(
        self,
        name: str,
        purpose: str,
        dependencies: Optional[List[str]] = None,
        can_run_parallel: bool = True,
        required_tools: Optional[List[str]] = None,
        execution_mode: str = "deterministic",
        selected_model: str = "none",
        planned_model: str = "none",
        model_status: str = "inactive",
        cost_tier: str = "none",
        provider: Optional[str] = None,
        deployment: Optional[str] = None,
        disabled_reason: Optional[str] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.purpose = purpose
        self.dependencies = dependencies or []
        self.can_run_parallel = can_run_parallel
        self.required_tools = required_tools or []
        self.execution_mode = execution_mode
        self.selected_model = selected_model
        self.planned_model = planned_model
        self.model_status = model_status
        self.cost_tier = cost_tier
        self.provider = provider
        self.deployment = deployment
        self.disabled_reason = disabled_reason
        self.status = "pending"  # pending, running, complete, failed
        self.result: Any = None
        self.error: Optional[str] = None
        self.latency_ms = 0
        self.token_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    def to_dict(self) -> Dict[str, Any]:
        res = {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "dependencies": self.dependencies,
            "status": self.status,
            "execution_mode": self.execution_mode,
            "model": self.selected_model,
            "planned_model": self.planned_model,
            "model_status": self.model_status,
            "cost_tier": self.cost_tier,
            "result": self.result,
            "error": self.error,
            "latency_ms": self.latency_ms,
        }
        if self.disabled_reason is not None:
            res["disabled_reason"] = self.disabled_reason
        if self.execution_mode == "model" or self.provider is not None:
            res["provider"] = self.provider
            res["deployment"] = self.deployment
            res["token_usage"] = self.token_usage
        return res


class TaskGraphExecutor:
    def __init__(self):
        self.nodes: Dict[str, TaskNode] = {}

    def add_node(self, node: TaskNode):
        self.nodes[node.name] = node

    async def execute_all(self, user_query: str, db: Optional[AsyncSession] = None) -> List[Dict[str, Any]]:
        """Task graph execution is not yet implemented — returns not_implemented node."""
        logger.info("Task Graph execution not implemented for query: '%s'", user_query)
        is_reconciliation = any(kw in user_query.lower() for kw in ["compare", "reconcile", "reconciliation", "credit note", "pdf"])
        if is_reconciliation:
            node = TaskNode(
                name="Task Graph",
                purpose="Parallel subtask execution for credit-note/PDF reconciliation",
                execution_mode="not_implemented",
                selected_model="none",
                planned_model="none",
                model_status="inactive",
                cost_tier="none",
                disabled_reason="Task graph parallel worker execution is not yet implemented. Sub-agent workers need real Odoo, attachment, and model integrations.",
            )
            node.status = "complete"
            node.result = None
            return [node.to_dict()]
        return []
