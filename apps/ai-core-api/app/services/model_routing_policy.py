import logging
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIModel, AIRoute, AIProvider

logger = logging.getLogger(__name__)


class ModelRoutingPolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def select_route(
        self,
        task_type: str,
        risk_level: str = "low",
        requires_tools: bool = False,
        requires_large_context: bool = False,
    ) -> Dict[str, Any]:
        """Determines the optimal model routing and fallback route based on request requirements.

        Returns a routing metadata dictionary.
        """
        logger.info(
            "Selecting route | task_type=%s risk_level=%s requires_tools=%s",
            task_type, risk_level, requires_tools
        )

        # 1. Map request characteristics to a specific task route
        selected_task_type = task_type
        reason = "matched_request_task_type"

        # Escalate to high quality / higher cost finance route on high-risk financial keywords
        if task_type == "general_chat" and risk_level == "high":
            selected_task_type = "finance"
            reason = "high_risk_escalation_to_finance_route"

        # 2. Query database for route mapping
        route_res = await self.db.execute(
            select(AIRoute).where(
                AIRoute.task_type == selected_task_type,
                AIRoute.enabled == "true"
            )
        )
        route = route_res.scalar_one_or_none()

        # Fallback to default general_chat route if specific route not found
        if not route and selected_task_type != "general_chat":
            route_res = await self.db.execute(
                select(AIRoute).where(
                    AIRoute.task_type == "general_chat",
                    AIRoute.enabled == "true"
                )
            )
            route = route_res.scalar_one_or_none()
            reason = f"fallback_to_default_general_chat_from_{selected_task_type}"

        if not route:
            # Absolute default fallback values if no routes configured in DB
            logger.warning("No active routing configurations found in database.")
            return {
                "selected_route_id": None,
                "selected_model_id": None,
                "fallback_model_id": None,
                "reason": "no_active_routes_in_db",
                "cost_tier": "medium",
                "quality_tier": "standard",
            }

        # 3. Load primary model details
        model_res = await self.db.execute(
            select(AIModel).where(AIModel.id == route.primary_model_id, AIModel.enabled == "true")
        )
        primary_model = model_res.scalar_one_or_none()

        if not primary_model:
            logger.warning("Primary model for route '%s' is disabled or missing", route.task_type)
            return {
                "selected_route_id": str(route.id),
                "selected_model_id": None,
                "fallback_model_id": None,
                "reason": "primary_model_unavailable",
                "cost_tier": "medium",
                "quality_tier": "standard",
            }

        # 4. Read metadata and capability limits from config_json or existing columns
        config = primary_model.config_json or {}
        cost_tier = config.get("cost_tier", "medium")
        quality_tier = config.get("quality_tier", "standard")

        # Verify that primary model supports required capabilities (like tools)
        supports_tools = primary_model.supports_tools == "true" or config.get("supports_tools") is True
        if requires_tools and not supports_tools:
            # Primary doesn't support tools, find a tool-capable fallback
            logger.warning("Primary model %s does not support required tools", primary_model.display_name)

        # 5. Load fallback model details if configured
        fallback_model_id = None
        if route.fallback_model_id:
            fb_model_res = await self.db.execute(
                select(AIModel).where(AIModel.id == route.fallback_model_id, AIModel.enabled == "true")
            )
            fallback_model = fb_model_res.scalar_one_or_none()
            if fallback_model:
                fallback_model_id = fallback_model.id

        return {
            "selected_route_id": str(route.id),
            "selected_model_id": str(primary_model.id),
            "fallback_model_id": str(fallback_model_id) if fallback_model_id else None,
            "reason": reason,
            "cost_tier": cost_tier,
            "quality_tier": quality_tier,
        }
