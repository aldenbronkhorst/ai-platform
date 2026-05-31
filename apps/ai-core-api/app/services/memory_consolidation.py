import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set
from uuid import UUID, uuid4
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIMemory, AITask, AIRule
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)


class MemoryConsolidationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _normalize_text(self, text: str) -> List[str]:
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

    def _calculate_similarity(self, tokens1: List[str], tokens2: List[str]) -> float:
        """Simple token overlap/Jaccard similarity coefficient."""
        if not tokens1 or not tokens2:
            return 0.0
        set1 = set(tokens1)
        set2 = set(tokens2)
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union) if union else 0.0

    async def consolidate_memories(self) -> Dict[str, Any]:
        """Scans PostgreSQL active memories for patterns, merges, and correction candidates.

        Generates AITasks and draft AIMemory/AIRules for admin review.
        """
        logger.info("Starting Memory Consolidation Service...")
        stats = {
            "clusters_reviewed": 0,
            "sop_candidates_created": 0,
            "rule_candidates_created": 0,
            "merge_tasks_created": 0,
            "archive_tasks_created": 0,
        }

        # 1. Load active memories
        result = await self.db.execute(
            select(AIMemory).where(AIMemory.status == "active")
        )
        active_memories = result.scalars().all()
        logger.info("Retrieved %d active memories for consolidation review", len(active_memories))

        if not active_memories:
            return stats

        # 2. Cluster memories by token similarity and metadata
        clusters: List[List[AIMemory]] = []
        for mem in active_memories:
            added_to_cluster = False
            mem_tokens = self._normalize_text(f"{mem.title} {mem.body or ''} {mem.summary or ''}")

            for cluster in clusters:
                # Compare against the first item in the cluster for simplicity
                lead = cluster[0]
                lead_tokens = self._normalize_text(f"{lead.title} {lead.body or ''} {lead.summary or ''}")

                # Check strict metadata overlap OR token similarity
                metadata_match = (
                    mem.type == lead.type and
                    mem.scope_type == lead.scope_type and
                    (mem.scope_value == lead.scope_value or (mem.scope_type == "global" and mem.scope_value is None and lead.scope_value is None))
                )

                similarity = self._calculate_similarity(mem_tokens, lead_tokens)

                if metadata_match or similarity > 0.25:
                    cluster.append(mem)
                    added_to_cluster = True
                    break

            if not added_to_cluster:
                clusters.append([mem])

        stats["clusters_reviewed"] = len(clusters)
        logger.info("Clustered active memories into %d distinct groups", len(clusters))

        # 3. Analyze each cluster to generate consolidation proposals
        for cluster in clusters:
            if len(cluster) < 2:
                continue  # Skip single-record clusters

            lead = cluster[0]
            # Map types
            resolved_cases = [m for m in cluster if m.type == "resolved_case"]
            corrections = [m for m in cluster if m.type == "correction"]
            preferences = [m for m in cluster if m.type == "preference"]

            # Pattern A: Repeated Resolved Cases -> Draft SOP/Procedure Candidate
            if len(resolved_cases) >= 2:
                # Merge into a clear procedure step-by-step
                source_ids = [str(m.id) for m in resolved_cases]
                title = f"SOP Candidate: {lead.title}"
                body_steps = []
                for idx, rc in enumerate(resolved_cases):
                    body_steps.append(f"{idx + 1}. {rc.body or rc.summary or rc.title}")
                body = "Step-by-Step Procedure:\n" + "\n".join(body_steps)

                # Create the draft SOP candidate
                sop_candidate = AIMemory(
                    id=uuid4(),
                    type="procedure",
                    title=title,
                    summary=f"SOP candidate compiled from {len(resolved_cases)} resolved cases.",
                    body=body,
                    scope_type=lead.scope_type or "global",
                    scope_value=lead.scope_value,
                    status="needs_review",  # Requires admin approval
                    risk_level="medium",
                    confidence="medium",
                    metadata_json={
                        "consolidation_source": "resolved_case_pattern",
                        "source_memory_ids": source_ids
                    }
                )
                self.db.add(sop_candidate)
                stats["sop_candidates_created"] += 1

                # Create AITask for the admin to approve/reject
                task = AITask(
                    title=f"Review SOP Proposal: {title}",
                    description=(
                        f"Consolidation job compiled {len(resolved_cases)} resolved cases into "
                        f"a single drafted procedure candidate (id={sop_candidate.id}). Please review and approve."
                    ),
                    status="open",
                    priority="medium",
                    linked_model="ai_memories",
                    linked_record_id=str(sop_candidate.id),
                )
                self.db.add(task)
                stats["merge_tasks_created"] += 1

                # Log Audit Event
                audit_svc = AuditService(self.db)
                await audit_svc.log_event(AIAuditEventCreate(
                    action_type="memory_consolidation_proposed",
                    target_model="ai_memories",
                    target_record_id=str(sop_candidate.id),
                    input_summary=f"Proposed SOP candidate '{title}' merging {len(resolved_cases)} resolved cases.",
                    risk_level="medium",
                    status="success",
                    output_summary=f"Sources: {', '.join(source_ids)}"
                ))

            # Pattern B: Repeated Corrections -> Draft Business Rule Candidate
            elif len(corrections) >= 2:
                # Check if it mentions "currency", "ZAR", "R", "dollar" or "USD"
                is_currency_related = any(
                    any(kw in (m.body or m.title or "").lower() for kw in ["currency", "zar", "usd", "dollar", "rand"])
                    for m in corrections
                )

                title = f"Candidate Business Rule: {lead.title}"
                if is_currency_related:
                    title = "Candidate Rule: Use confirmed company currency for financial displays"
                    body = "Always display financial values and invoices using the confirmed Odoo/company currency (such as ZAR / R). Do not assume USD ($) unless explicitly confirmed by Odoo metadata."
                else:
                    body = f"Proposed business rule directive: " + "; ".join((m.body or m.summary or m.title) for m in corrections)

                source_ids = [str(m.id) for m in corrections]

                # Create the draft candidate rule (high-risk, never auto-activated)
                rule_candidate = AIRule(
                    id=uuid4(),
                    workflow="general_chat",
                    title=title,
                    body=body,
                    status="draft",  # requires admin approval
                    priority=15,  # Medium priority rule
                    version=1,
                )
                self.db.add(rule_candidate)
                stats["rule_candidates_created"] += 1

                # Create review task for the candidate rule
                task = AITask(
                    title=f"Review Rule Proposal: {title}",
                    description=(
                        f"Consolidation job compiled {len(corrections)} corrections into "
                        f"a draft candidate business rule (id={rule_candidate.id}). Please review and enable."
                    ),
                    status="open",
                    priority="high",
                    linked_model="ai_rules",
                    linked_record_id=str(rule_candidate.id),
                )
                self.db.add(task)
                stats["merge_tasks_created"] += 1

                # Log Audit Event
                audit_svc = AuditService(self.db)
                await audit_svc.log_event(AIAuditEventCreate(
                    action_type="rule_consolidation_proposed",
                    target_model="ai_rules",
                    target_record_id=str(rule_candidate.id),
                    input_summary=f"Proposed candidate business rule '{title}' from {len(corrections)} corrections.",
                    risk_level="high",
                    status="success",
                    output_summary=f"Sources: {', '.join(source_ids)}"
                ))

            # Pattern C: Repeated Preferences/Notes -> Propose merge task
            elif len(preferences) >= 2:
                source_ids = [str(m.id) for m in preferences]
                title = f"Merge redundant preference notes: {lead.title}"

                task = AITask(
                    title=title,
                    description=(
                        f"Detected {len(preferences)} redundant or highly similar active preferences. "
                        f"Please consolidate source IDs: {', '.join(source_ids)}."
                    ),
                    status="open",
                    priority="low",
                    linked_model="ai_memories",
                    linked_record_id=str(lead.id),
                )
                self.db.add(task)
                stats["merge_tasks_created"] += 1

                # Log Audit Event
                audit_svc = AuditService(self.db)
                await audit_svc.log_event(AIAuditEventCreate(
                    action_type="preference_consolidation_proposed",
                    target_model="ai_memories",
                    target_record_id=str(lead.id),
                    input_summary=f"Proposed preference note merge task for {len(preferences)} records.",
                    risk_level="low",
                    status="success",
                    output_summary=f"Sources: {', '.join(source_ids)}"
                ))

        await self.db.flush()
        logger.info("Memory consolidation completed. Stats: %s", stats)
        return stats
