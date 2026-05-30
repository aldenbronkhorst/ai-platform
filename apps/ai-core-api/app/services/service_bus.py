"""Azure Service Bus integration — send and receive messages for async workloads.

Queues (defined in IaC infra/bicep/modules/serviceBus.bicep):
  - ai-jobs
  - ai-runner-requests
  - ai-artifact-processing
  - ai-search-indexing
  - ai-followups
  - ai-notifications
  - ai-automation-events

Message schemas are documented inline per queue.
"""
import json
import logging
import os
from typing import Any, AsyncIterator, Optional

from azure.servicebus import ServiceBusClient, ServiceBusMessage, ServiceBusReceiver
from azure.servicebus.aio import ServiceBusClient as AsyncServiceBusClient
from azure.servicebus.aio import ServiceBusReceiver as AsyncServiceBusReceiver
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

QUEUE_MEMORY_EXTRACTION = "ai-jobs"
QUEUE_RUNNER_REQUESTS = "ai-runner-requests"
QUEUE_ARTIFACT_PROCESSING = "ai-artifact-processing"
QUEUE_SEARCH_INDEXING = "ai-search-indexing"
QUEUE_FOLLOWUPS = "ai-followups"
QUEUE_NOTIFICATIONS = "ai-notifications"
QUEUE_AUTOMATION_EVENTS = "ai-automation-events"


def _get_namespace() -> Optional[str]:
    return (
        os.environ.get("AZURE_SERVICE_BUS_NAMESPACE")
        or os.environ.get("SERVICE_BUS_NAMESPACE")
    )


def _build_client(async_mode: bool = False):
    ns = _get_namespace()
    if not ns:
        return None
    fqdn = f"{ns}.servicebus.windows.net"
    credential = DefaultAzureCredential()
    if async_mode:
        return AsyncServiceBusClient(fully_qualified_namespace=fqdn, credential=credential)
    return ServiceBusClient(fully_qualified_namespace=fqdn, credential=credential)


# ── Synchronous helpers (for startup / non-async contexts) ──

def send_message_sync(queue_name: str, body: dict) -> bool:
    """Send a single message to a queue synchronously."""
    client = _build_client(async_mode=False)
    if not client:
        logger.warning("Service Bus not configured, skipping message to %s", queue_name)
        return False
    try:
        sender = client.get_queue_sender(queue_name)
        message = ServiceBusMessage(json.dumps(body))
        sender.send_messages(message)
        sender.close()
        logger.info("Sent message to %s: %s", queue_name, body)
        return True
    except Exception:
        logger.exception("Failed to send message to %s", queue_name)
        return False
    finally:
        client.close()


# ── Async helpers (for worker loops) ──

async def send_message_async(queue_name: str, body: dict) -> bool:
    """Send a single message asynchronously."""
    client = _build_client(async_mode=True)
    if not client:
        logger.warning("Service Bus not configured, skipping message to %s", queue_name)
        return False
    try:
        sender = client.get_queue_sender(queue_name)
        message = ServiceBusMessage(json.dumps(body))
        await sender.send_messages(message)
        await sender.close()
        logger.info("Sent async message to %s: %s", queue_name, body)
        return True
    except Exception:
        logger.exception("Failed to send async message to %s", queue_name)
        return False
    finally:
        await client.close()


async def receive_messages_async(
    queue_name: str,
    max_messages: int = 10,
    max_wait_time: float = 30.0,
) -> AsyncIterator[tuple[dict, Any]]:
    """Receive messages from a queue asynchronously.

    Yields (parsed_body, raw_message) tuples. Caller must call
    `raw_message.complete()` after processing.
    """
    client = _build_client(async_mode=True)
    if not client:
        logger.warning("Service Bus not configured, cannot receive from %s", queue_name)
        return

    receiver: Optional[AsyncServiceBusReceiver] = None
    try:
        receiver = client.get_queue_receiver(queue_name)
        messages = await receiver.receive_messages(
            max_message_count=max_messages,
            max_wait_time=max_wait_time,
        )
        async for msg in messages:
            try:
                body = json.loads(str(msg))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid message body: %s", msg)
                await receiver.dead_letter(msg)
                continue
            yield body, msg
    except Exception:
        logger.exception("Error receiving messages from %s", queue_name)
    finally:
        if receiver:
            await receiver.close()
        await client.close()


# ── Message Schemas ──

MEMORY_EXTRACTION_SCHEMA = {
    "queue": QUEUE_MEMORY_EXTRACTION,
    "description": "Trigger async memory extraction after a chat message is processed",
    "fields": {
        "message_type": "memory_extraction",
        "conversation_id": "UUID of the chat session",
        "user_id": "UUID of the user",
        "message_ids": ["list of message UUIDs to analyze (optional, latest pair if omitted)"],
    },
}

RUNNER_REQUEST_SCHEMA = {
    "queue": QUEUE_RUNNER_REQUESTS,
    "description": "Trigger a background runner (AI workflow / pipeline)",
    "fields": {
        "message_type": "runner_request",
        "runner_id": "UUID of the runner definition",
        "user_id": "UUID of the requesting user",
        "payload": "dict of input parameters",
    },
}

ARTIFACT_PROCESSING_SCHEMA = {
    "queue": QUEUE_ARTIFACT_PROCESSING,
    "description": "Process an uploaded artifact (OCR, vectorization, chunking)",
    "fields": {
        "message_type": "artifact_processing",
        "artifact_id": "UUID of the artifact",
        "user_id": "UUID of the uploading user",
    },
}

SEARCH_INDEXING_SCHEMA = {
    "queue": QUEUE_SEARCH_INDEXING,
    "description": "Trigger AI Search index rebuild or incremental update",
    "fields": {
        "message_type": "search_indexing",
        "index_name": "Name of the search index",
        "mode": "full | incremental",
        "source_ids": ["list of record UUIDs to reindex (optional)"],
    },
}

FOLLOWUP_SCHEMA = {
    "queue": QUEUE_FOLLOWUPS,
    "description": "Schedule a follow-up action (e.g., reminder, re-check)",
    "fields": {
        "message_type": "followup",
        "conversation_id": "UUID of the originating session",
        "user_id": "UUID of the user",
        "action": "reminder | recheck | escalation",
        "due_at": "ISO 8601 timestamp",
        "details": "dict with action-specific data",
    },
}

NOTIFICATION_SCHEMA = {
    "queue": QUEUE_NOTIFICATIONS,
    "description": "Push notification to the frontend or external channel",
    "fields": {
        "message_type": "notification",
        "user_id": "UUID of the target user",
        "title": "Notification title",
        "body": "Notification body text",
        "channel": "in_app | email | teams",
        "metadata_json": "dict with optional routing info",
    },
}

AUTOMATION_EVENT_SCHEMA = {
    "queue": QUEUE_AUTOMATION_EVENTS,
    "description": "Generic automation / webhook event for external integrations",
    "fields": {
        "message_type": "automation_event",
        "source": "Identifier of the originating system",
        "event": "Event name (e.g., 'invoice.paid', 'ticket.created')",
        "payload": "dict with event data",
    },
}
