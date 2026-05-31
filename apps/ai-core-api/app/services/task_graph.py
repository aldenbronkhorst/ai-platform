import logging
import asyncio
import time
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class TaskNode:
    def __init__(
        self,
        name: str,
        purpose: str,
        dependencies: Optional[List[str]] = None,
        can_run_parallel: bool = True,
        required_tools: Optional[List[str]] = None,
        selected_model: str = "none",
        cost_tier: str = "low",
    ):
        self.id = str(uuid4())
        self.name = name
        self.purpose = purpose
        self.dependencies = dependencies or []
        self.can_run_parallel = can_run_parallel
        self.required_tools = required_tools or []
        self.selected_model = selected_model
        self.cost_tier = cost_tier
        self.status = "pending"  # pending, running, complete, failed
        self.result: Any = None
        self.error: Optional[str] = None
        self.latency_ms = 0
        self.token_usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "dependencies": self.dependencies,
            "status": self.status,
            "model": self.selected_model,
            "cost_tier": self.cost_tier,
            "result": self.result,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
        }


class TaskGraphExecutor:
    def __init__(self):
        self.nodes: Dict[str, TaskNode] = {}

    def add_node(self, node: TaskNode):
        self.nodes[node.name] = node

    async def execute_all(self, user_query: str) -> List[Dict[str, Any]]:
        """Coordinates and executes the task graph nodes, matching parallel dependency paths."""
        logger.info("Starting Task Graph execution for query: '%s'", user_query)
        start_time = time.monotonic()

        # Step 1. Define standard parallel subtasks if query mentions comparison or reconciliation
        is_reconciliation = any(kw in user_query.lower() for kw in ["compare", "reconcile", "reconciliation", "credit note", "pdf"])

        if is_reconciliation:
            # 1. Odoo Data Worker (Deterministic, no model)
            self.add_node(TaskNode(
                name="Odoo Data Worker",
                purpose="Pull credit note header and line items from Odoo",
                dependencies=[],
                selected_model="none",
                cost_tier="none"
            ))
            # 2. PDF Extraction Worker (DeepSeek Flash)
            self.add_node(TaskNode(
                name="PDF Extraction Worker",
                purpose="Download and OCR attached credit note PDF",
                dependencies=[],
                selected_model="DeepSeek Flash",
                cost_tier="low"
            ))
            # 3. Reconciliation Worker (Qwen - depends on both extraction tasks)
            self.add_node(TaskNode(
                name="Reconciliation Worker",
                purpose="Compare Odoo lines versus PDF lines",
                dependencies=["Odoo Data Worker", "PDF Extraction Worker"],
                selected_model="Qwen",
                cost_tier="medium",
                can_run_parallel=False
            ))
        else:
            # Default single fallback worker task
            self.add_node(TaskNode(
                name="General Chat Worker",
                purpose="Answer basic user query",
                dependencies=[],
                selected_model="DeepSeek Flash",
                cost_tier="low"
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
                    node.token_usage = {"prompt_tokens": 150, "completion_tokens": 80}
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
                    node.token_usage = {"prompt_tokens": 450, "completion_tokens": 200}
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
