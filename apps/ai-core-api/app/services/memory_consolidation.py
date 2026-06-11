import logging
import re
from typing import Any
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIMemory, AITask
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)


class MemoryConsolidationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _initial_stats() -> dict[str, int]:
        return {
            "clusters_reviewed": 0,
            "sop_candidates_created": 0,
            "correction_review_tasks_created": 0,
            "merge_tasks_created": 0,
            "archive_tasks_created": 0,
        }

    def _normalize_text(self, text: str) -> list[str]:
        """Simple text normalization: lowercase, remove punctuation, split into tokens."""
        if not text:
            return []
        # Lowercase and remove punctuation
        text = text.lower()
        text = re.sub(r"[^\w\s-]", "", text)
        # Split and filter out short/common stop words
        stopwords = {
            "the", "and", "for", "are", "this", "that", "with", "from", "what", "how",
            "why", "use", "need", "should", "works", "fixed", "after", "select", "works",
            "worked", "always", "never", "only", "about", "your", "under"
        }
        tokens = [t for t in text.split() if len(t) > 2 and t not in stopwords]
        return tokens

    def _calculate_similarity(self, tokens1: list[str], tokens2: list[str]) -> float:
        """Simple token overlap/Jaccard similarity coefficient."""
        if not tokens1 or not tokens2:
            return 0.0
        set1 = set(tokens1)
        set2 = set(tokens2)
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union) if union else 0.0

    @staticmethod
    def _memory_text(memory: AIMemory) -> str:
        return f"{memory.title} {memory.body or ''} {memory.summary or ''}"

    @staticmethod
    def _metadata_matches(memory: AIMemory, lead: AIMemory) -> bool:
        scope_matches = (
            memory.scope_value == lead.scope_value
            or (
                memory.scope_type == "global"
                and memory.scope_value is None
                and lead.scope_value is None
            )
        )
        return memory.type == lead.type and memory.scope_type == lead.scope_type and scope_matches

    def _belongs_to_cluster(self, memory: AIMemory, lead: AIMemory, tokens_by_id: dict[UUID, list[str]]) -> bool:
        if self._metadata_matches(memory, lead):
            return True
        return self._calculate_similarity(tokens_by_id[memory.id], tokens_by_id[lead.id]) > 0.25

    def _cluster_memories(self, active_memories: list[AIMemory]) -> list[list[AIMemory]]:
        tokens_by_id = {memory.id: self._normalize_text(self._memory_text(memory)) for memory in active_memories}
        clusters: list[list[AIMemory]] = []
        for memory in active_memories:
            cluster = next(
                (candidate for candidate in clusters if self._belongs_to_cluster(memory, candidate[0], tokens_by_id)),
                None,
            )
            if cluster:
                cluster.append(memory)
            else:
                clusters.append([memory])
        return clusters

    def _add_review_task(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        linked_model: str,
        linked_record_id: str,
    ) -> None:
        self.db.add(AITask(
            title=title,
            description=description,
            status="open",
            priority=priority,
            linked_model=linked_model,
            linked_record_id=linked_record_id,
        ))

    async def _log_proposal(
        self,
        *,
        action_type: str,
        target_model: str,
        target_record_id: str,
        input_summary: str,
        risk_level: str,
        source_ids: list[str],
    ) -> None:
        await AuditService(self.db).log_event(AIAuditEventCreate(
            action_type=action_type,
            target_model=target_model,
            target_record_id=target_record_id,
            input_summary=input_summary,
            risk_level=risk_level,
            status="success",
            output_summary=f"Sources: {', '.join(source_ids)}",
        ))

    async def _create_sop_candidate(self, lead: AIMemory, resolved_cases: list[AIMemory], stats: dict[str, int]) -> None:
        source_ids = [str(memory.id) for memory in resolved_cases]
        title = f"SOP Candidate: {lead.title}"
        body = "Step-by-Step Procedure:\n" + "\n".join(
            f"{idx + 1}. {memory.body or memory.summary or memory.title}"
            for idx, memory in enumerate(resolved_cases)
        )

        sop_candidate = AIMemory(
            id=uuid4(),
            type="procedure",
            title=title,
            summary=f"SOP candidate compiled from {len(resolved_cases)} resolved cases.",
            body=body,
            scope_type=lead.scope_type or "global",
            scope_value=lead.scope_value,
            status="needs_review",
            risk_level="medium",
            confidence="medium",
            metadata_json={
                "consolidation_source": "resolved_case_pattern",
                "source_memory_ids": source_ids,
            },
        )
        self.db.add(sop_candidate)
        stats["sop_candidates_created"] += 1

        self._add_review_task(
            title=f"Review SOP Proposal: {title}",
            description=(
                f"Consolidation job compiled {len(resolved_cases)} resolved cases into "
                f"a single drafted procedure candidate (id={sop_candidate.id}). Please review and approve."
            ),
            priority="medium",
            linked_model="ai_memories",
            linked_record_id=str(sop_candidate.id),
        )
        stats["merge_tasks_created"] += 1

        await self._log_proposal(
            action_type="memory_consolidation_proposed",
            target_model="ai_memories",
            target_record_id=str(sop_candidate.id),
            input_summary=f"Proposed SOP candidate '{title}' merging {len(resolved_cases)} resolved cases.",
            risk_level="medium",
            source_ids=source_ids,
        )

    async def _create_correction_review_task(self, lead: AIMemory, corrections: list[AIMemory], stats: dict[str, int]) -> None:
        source_ids = [str(memory.id) for memory in corrections]
        title = f"Review repeated corrections: {lead.title}"

        self._add_review_task(
            title=title,
            description=(
                f"Detected {len(corrections)} related correction memories. "
                f"Please review whether these should be merged into a single approved memory. "
                f"Source IDs: {', '.join(source_ids)}."
            ),
            priority="high",
            linked_model="ai_memories",
            linked_record_id=str(lead.id),
        )
        stats["correction_review_tasks_created"] += 1
        stats["merge_tasks_created"] += 1

        await self._log_proposal(
            action_type="correction_consolidation_proposed",
            target_model="ai_memories",
            target_record_id=str(lead.id),
            input_summary=f"Proposed correction memory review task for {len(corrections)} records.",
            risk_level="high",
            source_ids=source_ids,
        )

    async def _create_preference_merge_task(self, lead: AIMemory, preferences: list[AIMemory], stats: dict[str, int]) -> None:
        source_ids = [str(memory.id) for memory in preferences]
        title = f"Merge redundant preference notes: {lead.title}"
        self._add_review_task(
            title=title,
            description=(
                f"Detected {len(preferences)} redundant or highly similar active preferences. "
                f"Please consolidate source IDs: {', '.join(source_ids)}."
            ),
            priority="low",
            linked_model="ai_memories",
            linked_record_id=str(lead.id),
        )
        stats["merge_tasks_created"] += 1

        await self._log_proposal(
            action_type="preference_consolidation_proposed",
            target_model="ai_memories",
            target_record_id=str(lead.id),
            input_summary=f"Proposed preference note merge task for {len(preferences)} records.",
            risk_level="low",
            source_ids=source_ids,
        )

    @staticmethod
    def _cluster_patterns(cluster: list[AIMemory]) -> tuple[list[AIMemory], list[AIMemory], list[AIMemory]]:
        return (
            [memory for memory in cluster if memory.type == "resolved_case"],
            [memory for memory in cluster if memory.type == "correction"],
            [memory for memory in cluster if memory.type == "preference"],
        )

    async def _review_cluster(self, cluster: list[AIMemory], stats: dict[str, int]) -> None:
        if len(cluster) < 2:
            return

        lead = cluster[0]
        resolved_cases, corrections, preferences = self._cluster_patterns(cluster)
        if len(resolved_cases) >= 2:
            await self._create_sop_candidate(lead, resolved_cases, stats)
        elif len(corrections) >= 2:
            await self._create_correction_review_task(lead, corrections, stats)
        elif len(preferences) >= 2:
            await self._create_preference_merge_task(lead, preferences, stats)

    async def consolidate_memories(self) -> dict[str, Any]:
        """Scans active memories and creates admin-review consolidation proposals."""
        logger.info("Starting Memory Consolidation Service...")
        stats = self._initial_stats()

        result = await self.db.execute(select(AIMemory).where(AIMemory.status == "active"))
        active_memories = result.scalars().all()
        logger.info("Retrieved %d active memories for consolidation review", len(active_memories))
        if not active_memories:
            return stats

        clusters = self._cluster_memories(active_memories)
        stats["clusters_reviewed"] = len(clusters)
        logger.info("Clustered active memories into %d distinct groups", len(clusters))
        for cluster in clusters:
            await self._review_cluster(cluster, stats)

        await self.db.flush()
        logger.info("Memory consolidation completed. Stats: %s", stats)
        return stats
