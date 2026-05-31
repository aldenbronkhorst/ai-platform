#!/usr/bin/env python3
"""Service Bus worker entrypoint for async memory extraction.

Runs as a long-lived process consuming messages from the `ai-jobs` queue.
Designed to be deployed as an Azure Container App (single replica).
"""
import asyncio
import logging
import os
import signal
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.service_bus import receive_messages_async, QUEUE_MEMORY_EXTRACTION
from app.services.memory_worker import process_memory_extraction_message
from app.services.audit import AuditService
from app.schemas.schemas import AIAuditEventCreate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("memory_worker")

# Graceful shutdown handling
_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Shutdown signal received (%s), finishing current batch...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


@asynccontextmanager
async def _db_session():
    """Yield a DB session and handle commit/rollback."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _process_single_message(body: dict, raw_msg) -> bool:
    """Process one Service Bus message. Returns True if successful."""
    conversation_id = body.get("conversation_id", "unknown")
    user_id = body.get("user_id", "unknown")
    job_id = f"{conversation_id}:{user_id}"

    logger.info("Job started | job_id=%s type=%s", job_id, body.get("message_type"))

    try:
        async with _db_session() as session:
            msg_type = body.get("message_type")
            if msg_type == "memory_extraction":
                await process_memory_extraction_message(body, session)
                action_text = "Memory extraction"
            elif msg_type == "memory_consolidation":
                from app.services.memory_consolidation import MemoryConsolidationService
                svc = MemoryConsolidationService(session)
                await svc.consolidate_memories()
                action_text = "Memory consolidation"
            else:
                logger.warning("Unknown message type: %s", msg_type)
                return False  # dead-letter unknown types

            # Write audit event for job completion
            audit = AuditService(session)
            await audit.log_event(AIAuditEventCreate(
                action_type=f"{msg_type}_complete" if msg_type else "job_complete",
                target_model="ai_chat_session" if msg_type == "memory_extraction" else "ai_memories",
                target_record_id=str(conversation_id) if msg_type == "memory_extraction" else str(uuid4()),
                actor_user_id=user_id if user_id != "unknown" else None,
                input_summary=f"{action_text} job completed successfully",
                risk_level="low",
                status="success",
            ))

        logger.info("Job succeeded | job_id=%s", job_id)
        return True

    except Exception as exc:
        logger.exception("Job failed | job_id=%s error=%s", job_id, exc)
        return False


async def _run_worker_loop():
    """Main worker loop: receive and process messages."""
    logger.info("Memory worker starting | queue=%s", QUEUE_MEMORY_EXTRACTION)

    while not _shutdown_requested:
        try:
            msg_count = 0
            async for body, raw_msg, receiver in receive_messages_async(
                queue_name=QUEUE_MEMORY_EXTRACTION,
                max_messages=5,
                max_wait_time=20.0,
            ):
                if _shutdown_requested:
                    break
                msg_count += 1
                logger.info("Received message %d from queue", msg_count)

                success = await _process_single_message(body, raw_msg)

                if success:
                    await receiver.complete_message(raw_msg)
                    logger.info("Message completed")
                else:
                    await receiver.abandon_message(raw_msg)
                    logger.info("Message abandoned")

            if msg_count == 0:
                logger.debug("No messages received in this cycle")

        except Exception:
            logger.exception("Worker loop error, restarting in 5s...")
            await asyncio.sleep(5)

    logger.info("Memory worker shutting down gracefully")


def main():
    logger.info("=" * 60)
    logger.info("AI Platform Memory Worker")
    logger.info("Queue: %s", QUEUE_MEMORY_EXTRACTION)
    logger.info("=" * 60)
    asyncio.run(_run_worker_loop())


if __name__ == "__main__":
    main()
