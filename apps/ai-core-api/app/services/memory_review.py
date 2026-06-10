import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIMemory, AITask
from app.schemas.schemas import AIAuditEventCreate
from app.services.audit import AuditService

logger = logging.getLogger(__name__)


class MemoryReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _initial_summary() -> dict[str, int]:
        return {
            "duplicate_tasks_created": 0,
            "stale_memories_flagged": 0,
            "low_confidence_memories_flagged": 0,
            "conflicts_detected": 0,
        }

    async def _active_memories(self) -> list[AIMemory]:
        result = await self.db.execute(select(AIMemory).where(AIMemory.status == "active"))
        memories = result.scalars().all()
        logger.info("Found %d active memories to review", len(memories))
        return list(memories)

    @staticmethod
    def _newer_and_older(mem1: AIMemory, mem2: AIMemory) -> tuple[AIMemory, AIMemory]:
        return (mem1, mem2) if mem1.created_at > mem2.created_at else (mem2, mem1)

    def _add_review_task(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        linked_record_id: str,
    ) -> None:
        self.db.add(AITask(
            title=title,
            description=description,
            status="open",
            priority=priority,
            linked_model="ai_memories",
            linked_record_id=linked_record_id,
        ))

    async def _log_event(
        self,
        *,
        action_type: str,
        target_record_id: str,
        input_summary: str,
        risk_level: str,
    ) -> None:
        await AuditService(self.db).log_event(AIAuditEventCreate(
            action_type=action_type,
            target_model="ai_memories",
            target_record_id=target_record_id,
            input_summary=input_summary,
            risk_level=risk_level,
            status="success",
        ))

    async def _flag_for_review(self, memory: AIMemory, now: datetime | None = None) -> None:
        memory.status = "needs_review"
        memory.updated_at = now or datetime.now(timezone.utc)

    @staticmethod
    def _same_title(mem1: AIMemory, mem2: AIMemory) -> bool:
        return mem1.title.strip().lower() == mem2.title.strip().lower()

    @staticmethod
    def _same_conflict_scope(mem1: AIMemory, mem2: AIMemory) -> bool:
        return (
            mem1.type == mem2.type
            and mem1.scope_type == mem2.scope_type
            and mem1.scope_value == mem2.scope_value
        )

    @staticmethod
    def _has_opposing_directives(mem1: AIMemory, mem2: AIMemory) -> bool:
        m1_text = (mem1.body or "").lower()
        m2_text = (mem2.body or "").lower()
        opposing_pairs = [
            ("upstairs", "downstairs"),
            ("enable", "disable"),
            ("always", "never"),
            ("draft", "posted"),
        ]
        return any(
            (left in m1_text and right in m2_text) or (right in m1_text and left in m2_text)
            for left, right in opposing_pairs
        )

    async def _mark_duplicate(self, mem1: AIMemory, mem2: AIMemory, summary: dict[str, int]) -> None:
        newer, older = self._newer_and_older(mem1, mem2)
        await self._flag_for_review(newer)
        self._add_review_task(
            title=f"Resolve Duplicate Memory: {newer.title}",
            description=(
                f"Duplicate detected between older memory (id={older.id}) and "
                f"newer memory (id={newer.id}). Propose merging them into a single record."
            ),
            priority="medium",
            linked_record_id=str(newer.id),
        )
        summary["duplicate_tasks_created"] += 1
        await self._log_event(
            action_type="memory_review_duplicate",
            target_record_id=str(newer.id),
            input_summary="Memory review flagged duplicate. Proposing merge.",
            risk_level="low",
        )

    async def _mark_conflict(self, mem1: AIMemory, mem2: AIMemory, summary: dict[str, int]) -> None:
        newer, older = self._newer_and_older(mem1, mem2)
        await self._flag_for_review(newer)
        self._add_review_task(
            title=f"Resolve Contradictory Memory: {newer.title}",
            description=(
                f"Contradiction detected between memory A (id={older.id}, text='{older.body}') "
                f"and memory B (id={newer.id}, text='{newer.body}'). "
                "Proposing to supersede older memory or merge with clarification."
            ),
            priority="high",
            linked_record_id=str(newer.id),
        )
        summary["conflicts_detected"] += 1
        await self._log_event(
            action_type="memory_review_conflict",
            target_record_id=str(newer.id),
            input_summary="Memory conflict detected. Proposing supersede.",
            risk_level="medium",
        )

    async def _review_pair(self, mem1: AIMemory, mem2: AIMemory, summary: dict[str, int]) -> None:
        if self._same_title(mem1, mem2):
            await self._mark_duplicate(mem1, mem2, summary)
        elif self._same_conflict_scope(mem1, mem2) and self._has_opposing_directives(mem1, mem2):
            await self._mark_conflict(mem1, mem2, summary)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    def _stale_reason(self, memory: AIMemory, now: datetime) -> str | None:
        if memory.stale_after:
            stale_at = self._ensure_utc(memory.stale_after)
            return f"Explicit stale date reached: {stale_at.isoformat()}" if now > stale_at else None

        if memory.last_used_at:
            return None

        created_at = self._ensure_utc(memory.created_at)
        if now - created_at > timedelta(days=180):
            return "Memory has not been used or confirmed since creation (>6 months ago)"
        return None

    @staticmethod
    def _is_failing(memory: AIMemory) -> bool:
        failures = memory.failure_count or 0
        successes = memory.success_count or 0
        return failures > 3 or (failures > successes and failures > 0)

    async def _mark_stale(self, memory: AIMemory, reason: str, now: datetime, summary: dict[str, int]) -> None:
        await self._flag_for_review(memory, now)
        self._add_review_task(
            title=f"Review Stale Memory: {memory.title}",
            description=f"This memory was flagged as stale/outdated. Reason: {reason}.",
            priority="low",
            linked_record_id=str(memory.id),
        )
        summary["stale_memories_flagged"] += 1
        await self._log_event(
            action_type="memory_review_stale",
            target_record_id=str(memory.id),
            input_summary=f"Memory review flagged stale memory. Reason: {reason}",
            risk_level="low",
        )

    async def _mark_low_confidence(self, memory: AIMemory, now: datetime, summary: dict[str, int]) -> None:
        memory.confidence = "low"
        await self._flag_for_review(memory, now)
        self._add_review_task(
            title=f"Review Failing Memory: {memory.title}",
            description=(
                f"This memory has high failure rates (success={memory.success_count or 0}, "
                f"failures={memory.failure_count or 0}). Propose archiving or correcting."
            ),
            priority="high",
            linked_record_id=str(memory.id),
        )
        summary["low_confidence_memories_flagged"] += 1
        await self._log_event(
            action_type="memory_review_low_confidence",
            target_record_id=str(memory.id),
            input_summary=f"Memory flagged as low confidence due to high failure rate ({memory.failure_count} failures)",
            risk_level="low",
        )

    async def _review_memory_health(self, memory: AIMemory, now: datetime, summary: dict[str, int]) -> None:
        stale_reason = self._stale_reason(memory, now)
        if stale_reason:
            await self._mark_stale(memory, stale_reason, now, summary)
        elif self._is_failing(memory):
            await self._mark_low_confidence(memory, now, summary)

    async def run_review_job(self) -> dict[str, Any]:
        """Runs the complete memory cleanup, stale detection, and conflict detection pipeline."""
        logger.info("Starting Memory Review Job...")
        summary = self._initial_summary()
        active_memories = await self._active_memories()

        for index, mem1 in enumerate(active_memories):
            for mem2 in active_memories[index + 1:]:
                await self._review_pair(mem1, mem2, summary)

        now = datetime.now(timezone.utc)
        for memory in active_memories:
            await self._review_memory_health(memory, now, summary)

        await self.db.flush()
        logger.info("Memory Review Job complete. Summary: %s", summary)
        return summary
