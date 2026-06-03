"""Azure Service Bus integration for async memory extraction."""
import json
import logging
import os
from typing import Any, AsyncIterator, Optional

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient as AsyncServiceBusClient
from azure.servicebus.aio import ServiceBusReceiver as AsyncServiceBusReceiver
from azure.identity.aio import DefaultAzureCredential as AsyncDefaultAzureCredential

logger = logging.getLogger(__name__)

QUEUE_MEMORY_EXTRACTION = "ai-jobs"


def _get_namespace() -> Optional[str]:
    return (
        os.environ.get("AZURE_SERVICE_BUS_NAMESPACE")
        or os.environ.get("SERVICE_BUS_NAMESPACE")
    )


def _build_client():
    ns = _get_namespace()
    if not ns:
        return None
    fqdn = f"{ns}.servicebus.windows.net"
    return AsyncServiceBusClient(fully_qualified_namespace=fqdn, credential=AsyncDefaultAzureCredential())

async def send_message_async(queue_name: str, body: dict) -> bool:
    """Send a single message asynchronously."""
    client = _build_client()
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
) -> AsyncIterator[tuple[dict, Any, Any]]:
    """Receive messages from a queue asynchronously.

    Yields (parsed_body, raw_message, receiver) tuples.
    """
    client = _build_client()
    if not client:
        logger.warning("Service Bus not configured, cannot receive from %s", queue_name)
        return

    receiver: Optional[AsyncServiceBusReceiver] = None
    try:
        receiver = client.get_queue_receiver(queue_name)
        logger.info("Receiving messages from %s | max=%d wait=%.1fs", queue_name, max_messages, max_wait_time)
        messages = await receiver.receive_messages(
            max_message_count=max_messages,
            max_wait_time=max_wait_time,
        )
        logger.info("Received %d messages from %s", len(messages), queue_name)
        for msg in messages:
            try:
                body = json.loads(str(msg))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid message body: %s", msg)
                await receiver.dead_letter_message(msg)
                continue
            yield body, msg, receiver
    except Exception:
        logger.exception("Error receiving messages from %s", queue_name)
    finally:
        if receiver:
            await receiver.close()
        await client.close()
