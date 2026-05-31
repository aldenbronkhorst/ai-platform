import logging
from typing import List, Dict, Any, Optional, Tuple
from uuid import UUID, uuid4
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIRule, AITask
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logger = logging.getLogger(__name__)

OPPOSING_DIRECTIVES = [
    ("always", "never"),
    ("include", "exclude"),
    ("allow", "block"),
    ("enable", "disable"),
    ("use", "do not use"),
    ("prioritise", "deprioritise"),
    ("prioritize", "deprioritize"),
    ("posted", "draft"),
    ("usd", "zar"),
    ("usd", "r"),
    ("$", "zar"),
    ("$", "r"),
    ("paid", "unpaid"),
    ("required", "optional"),
]

HIGH_RISK_KEYWORDS = [
    "revenue", "currency", "zar", "usd", "invoice", "customer", "supplier",
    "payment", "bill", "p&l", "pnl", "financial", "compliance", "priority"
]


class RuleConflictService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _detect_opposing_terms(self, text1: str, text2: str) -> List[Tuple[str, str]]:
        """Scans both rule texts for any contradictory directive pairs."""
        t1 = text1.lower()
        t2 = text2.lower()
        conflicts = []

        for w1, w2 in OPPOSING_DIRECTIVES:
            if (w1 in t1 and w2 in t2) or (w2 in t1 and w1 in t2):
                conflicts.append((w1, w2))
        return conflicts

    async def check_conflicts(self, candidate_rule: AIRule) -> Optional[Dict[str, Any]]:
        """Compares a candidate rule against existing active rules.

        Returns a conflict dict if any conflict is detected, otherwise None.
        """
        # 1. Fetch all other active rules
        result = await self.db.execute(
            select(AIRule).where(
                AIRule.id != candidate_rule.id,
                AIRule.status == "active"
            )
        )
        active_rules = result.scalars().all()

        for old_rule in active_rules:
            # 2. Check for overlapping scope
            scope_overlap = False
            overlap_details = {}

            if candidate_rule.scope_type == old_rule.scope_type and candidate_rule.scope_type is not None:
                # If scopes match (including 'global')
                if candidate_rule.scope_type == "global":
                    scope_overlap = True
                    overlap_details["scope_type"] = "global"
                elif candidate_rule.scope_value == old_rule.scope_value and candidate_rule.scope_value is not None:
                    scope_overlap = True
                    overlap_details["scope_type"] = candidate_rule.scope_type
                    overlap_details["scope_value"] = candidate_rule.scope_value

            # Check other explicit dimensions
            for field in ["department", "workflow", "supplier", "customer"]:
                cand_val = getattr(candidate_rule, field)
                old_val = getattr(old_rule, field)
                if cand_val is not None and cand_val == old_val:
                    scope_overlap = True
                    overlap_details[field] = cand_val

            if not scope_overlap:
                continue

            # 3. Check for contradictory directives inside overlapping scopes
            opposing = self._detect_opposing_terms(candidate_rule.body, old_rule.body)
            # Or if titles have opposing keywords
            opposing_title = self._detect_opposing_terms(candidate_rule.title, old_rule.title)
            
            # Combine them
            opposing_all = list(set(opposing + opposing_title))

            if opposing_all:
                # We detected a conflict! Determine severity
                body_all = (candidate_rule.body + " " + old_rule.body + " " + candidate_rule.title + " " + old_rule.title).lower()
                is_high_risk = any(kw in body_all for kw in HIGH_RISK_KEYWORDS)
                severity = "high" if is_high_risk else "medium"

                # Setup recommended actions
                rec_action = "supersede"
                if "currency" in body_all:
                    rec_action = "reject_new"  # Never allow conflicting currency rules

                return {
                    "conflicting_rule_id": str(old_rule.id),
                    "conflicting_rule_title": old_rule.title,
                    "reason": f"Opposing terms detected in overlapping scope: {opposing_all}",
                    "overlapping_scope": overlap_details,
                    "opposing_terms": [f"'{w1}' vs '{w2}'" for w1, w2 in opposing_all],
                    "severity": severity,
                    "recommended_action": rec_action
                }

        return None

    async def enforce_rule_governance(self, rule: AIRule, user_id: Optional[UUID] = None) -> bool:
        """Enforces rule conflict checks.

        If a conflict is detected, forces status to 'draft' or 'needs_review'
        and creates an AITask for admin review.

        Returns True if a conflict was detected and gated, False otherwise.
        """
        conflict = await self.check_conflicts(rule)
        if not conflict:
            return False

        # Force status to draft/needs_review to block silent activation of contradictory rules
        severity = conflict["severity"]
        original_status = rule.status
        rule.status = "draft" if severity == "high" else "needs_review"

        # Create AITask for admin review
        task = AITask(
            id=uuid4(),
            title=f"Resolve Rule Conflict: {rule.title}",
            description=(
                f"A {severity}-severity conflict was detected between new/updated rule (id={rule.id}) "
                f"and existing active rule '{conflict['conflicting_rule_title']}' (id={conflict['conflicting_rule_id']}). "
                f"Reason: {conflict['reason']}. Recommended action: '{conflict['recommended_action']}'."
            ),
            status="open",
            priority="high" if severity == "high" else "medium",
            linked_model="ai_rules",
            linked_record_id=str(rule.id),
            completion_check_payload={
                "conflict_details": conflict
            }
        )
        self.db.add(task)

        # Log AIAuditEvent
        audit_svc = AuditService(self.db)
        await audit_svc.log_event(AIAuditEventCreate(
            action_type="rule_conflict_detected",
            target_model="ai_rules",
            target_record_id=str(rule.id),
            actor_user_id=user_id,
            input_summary=(
                f"Rule conflict detected. Status forced from '{original_status}' to '{rule.status}' "
                f"to prevent contradictory rules from becoming active."
            ),
            risk_level=severity,
            status="success",
        ))

        return True
