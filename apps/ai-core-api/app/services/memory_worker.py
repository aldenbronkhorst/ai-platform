"""Service Bus worker for async memory extraction (Step 8 placeholder).

Designed to run as an Azure Container App job that consumes memory extraction
messages from Service Bus. When a chat completes, a message is enqueued with:
  - conversation_id
  - user_id

This worker processes the conversation, extracts memory candidates via
MemoryCandidateService, and saves/notifies as appropriate.

Not yet wired into the active deployment. Will be activated when Service Bus
consumers are deployed.
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def process_memory_extraction_message(message: dict[str, Any]) -> None:
    """Process a memory extraction message from Service Bus.

    Expected message shape:
    {
        "conversation_id": "uuid",
        "user_id": "uuid",
    }
    """
    conversation_id = message.get("conversation_id")
    user_id = message.get("user_id")
    if not conversation_id or not user_id:
        logger.warning("Invalid memory extraction message: %s", message)
        return

    logger.info(
        "Processing memory extraction | conversation=%s user_id=%s",
        conversation_id, user_id,
    )
    # TODO: Implement when Service Bus consumer is deployed
    # 1. Load conversation messages from DB
    # 2. Run MemoryCandidateService.extract_from_messages()
    # 3. Check duplicates
    # 4. Auto-save low-risk candidates
    # 5. Notify frontend for medium/high-risk candidates
    # 6. Log results
    logger.info(
        "Memory extraction complete (placeholder) | conversation=%s",
        conversation_id,
    )
