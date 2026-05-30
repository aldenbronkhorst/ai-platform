"""Service Bus worker for async memory extraction.

Designed to run as an Azure Container App job or as a long-running background
task within the API process. Consumes messages from the `ai-jobs` queue.

Lifecycle:
  1. Chat completes → post_chat_message enqueues a memory_extraction message
  2. This worker picks up the message
  3. Loads conversation messages from DB
  4. Runs MemoryCandidateService.extract_from_messages()
  5. Checks for duplicates
  6. Auto-saves low-risk candidates, flags medium/high for review
"""
import json
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, asc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AIChatMessage, AIMemory
from app.services.memory import MemoryCandidateService

logger = logging.getLogger(__name__)


async def process_memory_extraction_message(
    body: dict[str, Any],
    db: AsyncSession,
) -> None:
    """Process a memory extraction message from Service Bus.

    Expected message shape:
    {
        "message_type": "memory_extraction",
        "conversation_id": "uuid",
        "user_id": "uuid",
        "message_ids": ["uuid", ...]  # optional
    }
    """
    conversation_id_str = body.get("conversation_id")
    user_id_str = body.get("user_id")
    message_ids: list[str] | None = body.get("message_ids")

    if not conversation_id_str or not user_id_str:
        logger.warning("Invalid memory extraction message: missing conversation_id or user_id")
        return

    try:
        conversation_id = UUID(conversation_id_str)
        user_id = UUID(user_id_str)
    except ValueError as exc:
        logger.warning("Invalid UUID in memory extraction message: %s", exc)
        return

    msg_ids: list[UUID] | None = None
    if message_ids:
        try:
            msg_ids = [UUID(m) for m in message_ids]
        except ValueError as exc:
            logger.warning("Invalid message UUID in extraction message: %s", exc)
            return

    logger.info(
        "Processing memory extraction | conversation=%s user_id=%s",
        conversation_id, user_id,
    )

    # 1. Load conversation messages from DB
    stmt = (
        select(AIChatMessage)
        .where(AIChatMessage.chat_session_id == conversation_id)
        .order_by(asc(AIChatMessage.created_at))
    )
    if msg_ids:
        stmt = stmt.where(AIChatMessage.id.in_(msg_ids))

    result = await db.execute(stmt)
    messages = result.scalars().all()

    if not messages:
        logger.info("No messages found for conversation %s", conversation_id)
        return

    logger.info("Loaded %d messages for conversation %s", len(messages), conversation_id)

    # 2. Run MemoryCandidateService.extract_from_messages()
    svc = MemoryCandidateService(db)
    candidates = await svc.extract_from_messages(messages, user_id=user_id)

    if not candidates:
        logger.info("No memory candidates found for conversation %s", conversation_id)
        return

    logger.info("Found %d memory candidates for conversation %s", len(candidates), conversation_id)

    # 4. Check duplicates and auto-save / flag
    saved_count = 0
    flagged_count = 0
    for candidate in candidates:
        is_dup = await svc.check_duplicate(candidate)
        if is_dup:
            logger.info("Skipping duplicate candidate: %s", candidate.title)
            continue

        if candidate.save_mode == "auto":
            memory = await svc.save_candidate(candidate, conversation_id=conversation_id, user_id=user_id)
            if memory:
                saved_count += 1
                logger.info("Auto-saved memory: %s (id=%s)", memory.title, memory.id)
                try:
                    from app.services.search_service import SearchService
                    search_svc = SearchService()
                    await search_svc.index_memory_record(memory)
                except Exception as e:
                    logger.warning("Failed to index auto-saved memory in search index: %s", e)
        else:
            # Flag for admin review (store as draft with pending review)
            memory = await svc.save_candidate(candidate, conversation_id=conversation_id, user_id=user_id)
            if memory:
                flagged_count += 1
                logger.info(
                    "Flagged memory for review: %s (id=%s, risk=%s)",
                    memory.title, memory.id, candidate.risk_level,
                )

    logger.info(
        "Memory extraction complete | conversation=%s saved=%d flagged=%d",
        conversation_id, saved_count, flagged_count,
    )
