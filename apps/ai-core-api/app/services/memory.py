"""MemoryCandidateService: extracts and manages memory candidates from chat conversations.

Observes completed chats, detects useful knowledge, classifies it, and
proposes save/update/archive actions based on risk and confidence.
"""
import logging
import re
from typing import Optional, Any
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_

from app.models.models import AIMemory, AIChatMessage, AIRule
from app.schemas.schemas import MemoryCandidate


def _escape_like(s: str) -> str:
    return s.replace("%", "\\%").replace("_", "\\_")

logger = logging.getLogger(__name__)

MEMORY_EXPLICIT_PATTERNS = [
    re.compile(r"\bremember\s+this\b", re.IGNORECASE),
    re.compile(r"\bremember\s+that\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+now\s+on\b", re.IGNORECASE),
    re.compile(r"\balways\s+(do|use|check|show|format)\b", re.IGNORECASE),
    re.compile(r"\bnever\s+(do|use|assume|show)\b", re.IGNORECASE),
]

CORRECTION_PATTERNS = [
    re.compile(r"\bno[,.]?\s+that['']?s\s+(wrong|not|incorrect)\b", re.IGNORECASE),
    re.compile(r"\bthat['']?s\s+not\s+(right|correct|what\s+I\s+meant)\b", re.IGNORECASE),
    re.compile(r"\byou['']?re\s+wrong\b", re.IGNORECASE),
    re.compile(r"\bdon['']?t\s+assume\b", re.IGNORECASE),
    re.compile(r"\bnot\s+(dollars|usd|zar|eur)\b", re.IGNORECASE),
    re.compile(r"\bshould\s+be\s+(zar|usd|eur|rand|dollar)\b", re.IGNORECASE),
]

RESOLVED_PATTERNS = [
    re.compile(r"\b(that|it)\s+worked\b", re.IGNORECASE),
    re.compile(r"\bfixed\b", re.IGNORECASE),
    re.compile(r"\bsolved\b", re.IGNORECASE),
    re.compile(r"\bthanks[,.]?\s+that\s+(worked|helped|solved\s+it)\b", re.IGNORECASE),
    re.compile(r"\bthanks[,.]?\s+for\s+(the\s+)?help\b", re.IGNORECASE),
]


class MemoryCandidateService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def extract_from_messages(
        self,
        messages: list[AIChatMessage],
        user_id: UUID,
    ) -> list[MemoryCandidate]:
        """Analyze a conversation and extract memory candidates."""
        candidates: list[MemoryCandidate] = []

        for msg in messages:
            if msg.role != "user":
                continue
            content = msg.content or ""
            candidates.extend(self._check_explicit_requests(content))
            candidates.extend(self._check_corrections(content))
            candidates.extend(self._check_resolved(content))

        # Deduplicate by type+title
        seen: set[tuple[str, str]] = set()
        unique: list[MemoryCandidate] = []
        for c in candidates:
            key = (c.type, c.title)
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique

    def _check_explicit_requests(self, content: str) -> list[MemoryCandidate]:
        """Detect 'remember this' and 'from now on' patterns."""
        candidates: list[MemoryCandidate] = []

        for pat in MEMORY_EXPLICIT_PATTERNS:
            match = pat.search(content)
            if match:
                # Try to extract the actual instruction after the keyword
                body = self._extract_instruction_after(content, match.end())
                if not body:
                    body = content.strip()
                candidates.append(MemoryCandidate(
                    type="system_behavior",
                    title=f"Learned behavior: {body[:80]}",
                    body=body,
                    confidence="medium",
                    risk_level="medium",
                    save_mode="confirm",
                ))
        return candidates

    def _check_corrections(self, content: str) -> list[MemoryCandidate]:
        """Detect corrections from the user."""
        for pat in CORRECTION_PATTERNS:
            if pat.search(content):
                return [MemoryCandidate(
                    type="correction",
                    title=f"Correction from user",
                    body=content.strip(),
                    confidence="medium",
                    risk_level="medium",
                    save_mode="confirm",
                )]
        return []

    def _check_resolved(self, content: str) -> list[MemoryCandidate]:
        """Detect resolved-case markers."""
        for pat in RESOLVED_PATTERNS:
            if pat.search(content):
                return [MemoryCandidate(
                    type="resolved_case",
                    title=f"Resolved: {content[:80]}",
                    body=content.strip(),
                    confidence="medium",
                    risk_level="low",
                    save_mode="auto",
                )]
        return []

    def _extract_instruction_after(self, content: str, start: int) -> str:
        """Extract the meaningful instruction text after a keyword match."""
        after = content[start:].strip()
        after = re.sub(r'^[,.:;!?\s]+', '', after)
        return after[:500]

    async def check_duplicate(
        self,
        candidate: MemoryCandidate,
    ) -> bool:
        """Check if a similar active memory already exists."""
        result = await self.db.execute(
            select(AIMemory).where(
                AIMemory.type == candidate.type,
                AIMemory.status == "active",
                AIMemory.title.ilike(f"%{_escape_like(candidate.title[:50])}%"),
            )
        )
        existing = result.scalar_one_or_none()
        return existing is not None

    async def save_candidate(
        self,
        candidate: MemoryCandidate,
        user_id: UUID,
        conversation_id: Optional[UUID] = None,
        message_id: Optional[UUID] = None,
    ) -> AIMemory:
        """Save a memory candidate to the database."""
        memory = AIMemory(
            type=candidate.type,
            title=candidate.title,
            summary=candidate.summary,
            body=candidate.body,
            scope_type=candidate.scope_type,
            scope_value=candidate.scope_value,
            entities_json=candidate.entities_json,
            confidence=candidate.confidence,
            risk_level=candidate.risk_level,
            status="active" if candidate.save_mode == "auto" else "draft",
            conversation_id=conversation_id,
            message_id=message_id,
            created_by_user_id=user_id,
        )
        self.db.add(memory)
        await self.db.flush()
        logger.info(
            "Memory saved | type=%s save_mode=%s risk=%s user_id=%s memory_id=%s",
            candidate.type, candidate.save_mode, candidate.risk_level,
            user_id, memory.id,
        )
        return memory
