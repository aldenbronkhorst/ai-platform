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
        """Coordinates and executes the task graph nodes, matching parallel dependency paths."""
        logger.info("Starting Task Graph execution for query: '%s'", user_query)
        start_time = time.monotonic()

        ds_active = False
        qw_active = False
        ds_provider_name = None
        ds_deployment_name = None
        qw_provider_name = None
        qw_deployment_name = None

        if db is not None:
            try:
                # 1. Query DeepSeek Flash
                ds_res = await db.execute(
                    select(AIModel, AIProvider)
                    .join(AIProvider, AIModel.provider_id == AIProvider.id)
                    .where(
                        AIModel.model_name == "DeepSeek-V4-Flash",
                        AIModel.enabled == "true",
                        AIProvider.enabled == "true"
                    )
                )
                ds_row = ds_res.first()
                if ds_row:
                    ds_active = True
                    ds_model, ds_prov = ds_row
                    ds_provider_name = ds_prov.name
                    ds_deployment_name = ds_model.deployment_name

                # 2. Query Qwen
                qw_res = await db.execute(
                    select(AIModel, AIProvider)
                    .join(AIProvider, AIModel.provider_id == AIProvider.id)
                    .where(
                        AIModel.model_name == "Qwen2.5-72B-Instruct",
                        AIModel.enabled == "true",
                        AIProvider.enabled == "true"
                    )
                )
                qw_row = qw_res.first()
                if qw_row:
                    qw_active = True
                    qw_model, qw_prov = qw_row
                    qw_provider_name = qw_prov.name
                    qw_deployment_name = qw_model.deployment_name
            except Exception as e:
                logger.warning("Database query in TaskGraphExecutor failed, defaulting to inactive: %s", e)

        # Step 1. Define standard parallel subtasks if query mentions comparison or reconciliation
        is_reconciliation = any(kw in user_query.lower() for kw in ["compare", "reconcile", "reconciliation", "credit note", "pdf"])

        if is_reconciliation:
            # 1. Odoo Data Worker (Deterministic, no model)
            self.add_node(TaskNode(
                name="Odoo Data Worker",
                purpose="Pull credit note header and line items from Odoo",
                dependencies=[],
                execution_mode="deterministic",
                selected_model="none",
                planned_model="none",
                model_status="inactive",
                cost_tier="none"
            ))
            # 2. PDF Extraction Worker (DeepSeek Flash)
            if ds_active:
                self.add_node(TaskNode(
                    name="PDF Extraction Worker",
                    purpose="Download and OCR attached credit note PDF",
                    dependencies=[],
                    execution_mode="model",
                    selected_model="DeepSeek Flash",
                    planned_model="DeepSeek Flash",
                    model_status="active",
                    cost_tier="low",
                    provider=ds_provider_name,
                    deployment=ds_deployment_name
                ))
            else:
                self.add_node(TaskNode(
                    name="PDF Extraction Worker",
                    purpose="Download and OCR attached credit note PDF",
                    dependencies=[],
                    execution_mode="deterministic",
                    selected_model="none",
                    planned_model="DeepSeek Flash",
                    model_status="inactive",
                    cost_tier="none"
                ))

            # 3. Reconciliation Worker (Qwen - depends on both extraction tasks)
            if qw_active:
                self.add_node(TaskNode(
                    name="Reconciliation Worker",
                    purpose="Compare Odoo lines versus PDF lines",
                    dependencies=["Odoo Data Worker", "PDF Extraction Worker"],
                    execution_mode="model",
                    selected_model="Qwen",
                    planned_model="Qwen Max",
                    model_status="active",
                    cost_tier="medium",
                    provider=qw_provider_name,
                    deployment=qw_deployment_name,
                    can_run_parallel=False
                ))
            else:
                self.add_node(TaskNode(
                    name="Reconciliation Worker",
                    purpose="Compare Odoo lines versus PDF lines",
                    dependencies=["Odoo Data Worker", "PDF Extraction Worker"],
                    execution_mode="deterministic",
                    selected_model="none",
                    planned_model="Qwen Max",
                    model_status="inactive",
                    cost_tier="none",
                    disabled_reason="DashScope provider integration required",
                    can_run_parallel=False
                ))
        else:
            # Default single fallback worker task
            if ds_active:
                self.add_node(TaskNode(
                    name="General Chat Worker",
                    purpose="Answer basic user query",
                    dependencies=[],
                    execution_mode="model",
                    selected_model="DeepSeek Flash",
                    planned_model="DeepSeek Flash",
                    model_status="active",
                    cost_tier="low",
                    provider=ds_provider_name,
                    deployment=ds_deployment_name
                ))
            else:
                self.add_node(TaskNode(
                    name="General Chat Worker",
                    purpose="Answer basic user query",
                    dependencies=[],
                    execution_mode="deterministic",
                    selected_model="none",
                    planned_model="DeepSeek Flash",
                    model_status="inactive",
                    cost_tier="none"
                ))

        # Helper to execute a single task node with simulated latency and outputs
        async def run_node(node: TaskNode):
            node.status = "running"
            node_start = time.monotonic()
            logger.info("Starting subtask: %s", node.name)

            try:
                if node.name == "Odoo Data Worker":
                    await asyncio.sleep(1.2)  # Simulate API call latency
                    node.result = {
                        "credit_note_number": "CN-2026-0012",
                        "total_amount": 25000.0,
                        "currency": "ZAR",
                        "lines": [
                            {"item": "Server Hosting", "qty": 1, "price": 15000.0},
                            {"item": "Consulting Fees", "qty": 10, "price": 1000.0}
                        ]
                    }
                elif node.name == "PDF Extraction Worker":
                    await asyncio.sleep(1.8)  # Simulate OCR latency
                    node.result = {
                        "pdf_filename": "credit_note_reconcile.pdf",
                        "extracted_lines": [
                            {"item": "Server Hosting", "qty": 1, "price": 15000.0},
                            {"item": "Consulting Fees", "qty": 10, "price": 1050.0}  # Discrepancy!
                        ]
                    }
                    if node.execution_mode == "model":
                        node.token_usage = {"prompt_tokens": 150, "completion_tokens": 80}
                    else:
                        node.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
                elif node.name == "Reconciliation Worker":
                    # Wait for dependencies first
                    while self.nodes["Odoo Data Worker"].status != "complete" or self.nodes["PDF Extraction Worker"].status != "complete":
                        await asyncio.sleep(0.1)
                    await asyncio.sleep(1.0)
                    node.result = {
                        "status": "discrepancies_found",
                        "discrepancies": [
                            {
                                "item": "Consulting Fees",
                                "odoo_price": 1000.0,
                                "pdf_price": 1050.0,
                                "difference": -50.0
                            }
                        ]
                    }
                    if node.execution_mode == "model":
                        node.token_usage = {"prompt_tokens": 450, "completion_tokens": 200}
                    else:
                        node.token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
                else:
                    await asyncio.sleep(0.5)
                    node.result = "Simple chat response compiled."

                node.status = "complete"
                node.latency_ms = int((time.monotonic() - node_start) * 1000)
                logger.info("Subtask %s finished successfully in %d ms", node.name, node.latency_ms)

            except Exception as e:
                node.status = "failed"
                node.error = str(e)
                node.latency_ms = int((time.monotonic() - node_start) * 1000)
                logger.error("Subtask %s failed: %s", node.name, e)

        # Step 2. Execute parallel tasks concurrently
        parallel_tasks = [
            run_node(node)
            for node in self.nodes.values()
            if len(node.dependencies) == 0
        ]
        
        if parallel_tasks:
            await asyncio.gather(*parallel_tasks)

        # Step 3. Execute sequential dependent tasks
        dependent_tasks = [
            run_node(node)
            for node in self.nodes.values()
            if len(node.dependencies) > 0
        ]
        
        if dependent_tasks:
            await asyncio.gather(*dependent_tasks)

        logger.info("Task Graph execution finished in %.2fs", time.monotonic() - start_time)
        return [node.to_dict() for node in self.nodes.values()]
