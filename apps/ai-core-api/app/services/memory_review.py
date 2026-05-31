import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy import select, or_, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIMemory, AITask
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate
from app.services.search_service import SearchService

logger = logging.getLogger(__name__)


class MemoryReviewService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.search_svc = SearchService()

    async def run_review_job(self) -> Dict[str, Any]:
        """Runs the complete memory cleanup, stale detection, and conflict detection pipeline.

        Returns a summary dictionary of actions taken.
        """
        logger.info("Starting Memory Review Job...")
        summary = {
            "duplicate_tasks_created": 0,
            "stale_memories_flagged": 0,
            "low_confidence_memories_flagged": 0,
            "conflicts_detected": 0,
        }

        # 1. Fetch all active memories
        result = await self.db.execute(
            select(AIMemory).where(AIMemory.status == "active")
        )
        active_memories = result.scalars().all()
        logger.info("Found %d active memories to review", len(active_memories))

        # 2. Duplicate & Simple Conflict Detection (Deterministic Overlaps)
        checked_pairs = set()
        for i, mem1 in enumerate(active_memories):
            for j, mem2 in enumerate(active_memories):
                if i >= j:
                    continue
                pair_key = tuple(sorted([str(mem1.id), str(mem2.id)]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)

                # Check exact title duplicates (case-insensitive)
                if mem1.title.strip().lower() == mem2.title.strip().lower():
                    # Flag the newer one as duplicate and create a review task
                    newer = mem1 if mem1.created_at > mem2.created_at else mem2
                    older = mem2 if mem1.created_at > mem2.created_at else mem1

                    newer.status = "needs_review"
                    newer.updated_at = datetime.now(timezone.utc)

                    task = AITask(
                        title=f"Resolve Duplicate Memory: {newer.title}",
                        description=(
                            f"Duplicate detected between older memory (id={older.id}) and "
                            f"newer memory (id={newer.id}). Propose merging them into a single record."
                        ),
                        status="open",
                        priority="medium",
                        linked_model="ai_memories",
                        linked_record_id=str(newer.id),
                    )
                    self.db.add(task)
                    summary["duplicate_tasks_created"] += 1

                    # Remove from search index
                    await self.search_svc.delete_memory_record(newer.id)

                    # Log audit event
                    audit = AuditService(self.db)
                    await audit.log_event(AIAuditEventCreate(
                        action_type="memory_review_duplicate",
                        target_model="ai_memories",
                        target_record_id=str(newer.id),
                        input_summary=f"Memory review flagged duplicate. Proposing merge.",
                        risk_level="low",
                        status="success",
                    ))

                # Check for simple contradictory content (conflict)
                # Same type, same scope, same primary keywords, but opposing descriptions/instructions
                elif mem1.type == mem2.type and mem1.scope_type == mem2.scope_type and mem1.scope_value == mem2.scope_value:
                    # Look for opposing directives or keyword matches
                    m1_text = (mem1.body or "").lower()
                    m2_text = (mem2.body or "").lower()

                    has_conflict = False
                    opposing_pairs = [
                        ("upstairs", "downstairs"),
                        ("enable", "disable"),
                        ("always", "never"),
                        ("draft", "posted"),
                    ]
                    for w1, w2 in opposing_pairs:
                        if (w1 in m1_text and w2 in m2_text) or (w2 in m1_text and w1 in m2_text):
                            has_conflict = True
                            break

                    if has_conflict:
                        # Flag both or the newer one for review
                        newer = mem1 if mem1.created_at > mem2.created_at else mem2
                        older = mem2 if mem1.created_at > mem2.created_at else mem1

                        newer.status = "needs_review"
                        newer.updated_at = datetime.now(timezone.utc)

                        task = AITask(
                            title=f"Resolve Contradictory Memory: {newer.title}",
                            description=(
                                f"Contradiction detected between memory A (id={older.id}, text='{older.body}') "
                                f"and memory B (id={newer.id}, text='{newer.body}'). "
                                f"Proposing to supersede older memory or merge with clarification."
                            ),
                            status="open",
                            priority="high",
                            linked_model="ai_memories",
                            linked_record_id=str(newer.id),
                        )
                        self.db.add(task)
                        summary["conflicts_detected"] += 1

                        # Sync with search
                        await self.search_svc.delete_memory_record(newer.id)

                        audit = AuditService(self.db)
                        await audit.log_event(AIAuditEventCreate(
                            action_type="memory_review_conflict",
                            target_model="ai_memories",
                            target_record_id=str(newer.id),
                            input_summary=f"Memory conflict detected. Proposing supersede.",
                            risk_level="medium",
                            status="success",
                        ))

        # 3. Outdated/Stale Memory Detection
        now = datetime.now(timezone.utc)
        for mem in active_memories:
            # Check explicit expiration (stale_after)
            is_stale = False
            reason = ""
            if mem.stale_after:
                # Ensure tzinfo is present
                stale_at = mem.stale_after
                if stale_at.tzinfo is None:
                    stale_at = stale_at.replace(tzinfo=timezone.utc)
                if now > stale_at:
                    is_stale = True
                    reason = f"Explicit stale date reached: {stale_at.isoformat()}"

            # Check unused stale date (null last_used_at and created_at older than 6 months)
            elif not mem.last_used_at:
                created_at = mem.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if now - created_at > timedelta(days=180):
                    is_stale = True
                    reason = "Memory has not been used or confirmed since creation (>6 months ago)"

            if is_stale:
                mem.status = "needs_review"
                mem.updated_at = now

                task = AITask(
                    title=f"Review Stale Memory: {mem.title}",
                    description=f"This memory was flagged as stale/outdated. Reason: {reason}.",
                    status="open",
                    priority="low",
                    linked_model="ai_memories",
                    linked_record_id=str(mem.id),
                )
                self.db.add(task)
                summary["stale_memories_flagged"] += 1

                # Sync with search
                await self.search_svc.delete_memory_record(mem.id)

                audit = AuditService(self.db)
                await audit.log_event(AIAuditEventCreate(
                    action_type="memory_review_stale",
                    target_model="ai_memories",
                    target_record_id=str(mem.id),
                    input_summary=f"Memory review flagged stale memory. Reason: {reason}",
                    risk_level="low",
                    status="success",
                ))

            # 4. Low Confidence & High Failure Rates
            elif (mem.failure_count or 0) > 3 or ((mem.failure_count or 0) > (mem.success_count or 0) and (mem.failure_count or 0) > 0):
                mem.confidence = "low"
                mem.status = "needs_review"
                mem.updated_at = now

                task = AITask(
                    title=f"Review Failing Memory: {mem.title}",
                    description=(
                        f"This memory has high failure rates (success={mem.success_count or 0}, "
                        f"failures={mem.failure_count or 0}). Propose archiving or correcting."
                    ),
                    status="open",
                    priority="high",
                    linked_model="ai_memories",
                    linked_record_id=str(mem.id),
                )
                self.db.add(task)
                summary["low_confidence_memories_flagged"] += 1

                # Sync with search
                await self.search_svc.delete_memory_record(mem.id)

                audit = AuditService(self.db)
                await audit.log_event(AIAuditEventCreate(
                    action_type="memory_review_low_confidence",
                    target_model="ai_memories",
                    target_record_id=str(mem.id),
                    input_summary=f"Memory flagged as low confidence due to high failure rate ({mem.failure_count} failures)",
                    risk_level="low",
                    status="success",
                ))

        await self.db.flush()
        logger.info("Memory Review Job complete. Summary: %s", summary)
        return summary
