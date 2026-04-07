"""Async Service Bus consumer for job-completed and job-failed queues."""
import asyncio
import json
import logging
from azure.servicebus.aio import ServiceBusClient
from app.config import settings
from app.db import get_user_email, get_job
from app.notifier import send_success_notification, send_failure_notification

logger = logging.getLogger(__name__)


async def process_completed_message(body: dict) -> None:
    job_id = body.get("job_id", "")
    user_id = body.get("user_id", "")
    output_url = body.get("output_url", "")

    email = await get_user_email(user_id)
    if not email:
        logger.warning("No email found for user %s", user_id)
        return

    job = await get_job(job_id)
    prompt = job.get("prompt", "") if job else ""

    await send_success_notification(
        recipient=email,
        prompt=prompt,
        output_url=output_url,
    )


async def process_failed_message(body: dict) -> None:
    job_id = body.get("job_id", "")
    user_id = body.get("user_id", "")
    error = body.get("error", "Unknown error")

    email = await get_user_email(user_id)
    if not email:
        logger.warning("No email found for user %s", user_id)
        return

    await send_failure_notification(
        recipient=email,
        job_id=job_id,
        error=error,
    )


_BACKOFF_BASE = 2
_BACKOFF_MAX = 60


async def consume_queue(queue_name: str, handler) -> None:
    attempt = 0
    while True:
        try:
            async with ServiceBusClient.from_connection_string(
                settings.azure_service_bus_connection_string
            ) as client:
                receiver = client.get_queue_receiver(queue_name=queue_name, max_wait_time=5)
                async with receiver:
                    attempt = 0
                    logger.info("Connected to Service Bus queue: %s", queue_name)
                    while True:
                        messages = await receiver.receive_messages(max_message_count=1, max_wait_time=5)
                        for msg in messages:
                            try:
                                body = json.loads(str(msg))
                                await handler(body)
                                await receiver.complete_message(msg)
                            except Exception as exc:
                                logger.error("Failed to process %s message: %s", queue_name, exc)
                                await receiver.abandon_message(msg)
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Consumer task for %s cancelled; shutting down", queue_name)
            raise
        except Exception as exc:
            wait = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)
            logger.warning(
                "Service Bus connection failed for %s (attempt %d), retrying in %ss: %s",
                queue_name, attempt + 1, wait, exc,
            )
            attempt += 1
            await asyncio.sleep(wait)


async def run_consumer() -> None:
    logger.info("Starting notification consumer for job-completed and job-failed queues")
    await asyncio.gather(
        consume_queue("job-completed", process_completed_message),
        consume_queue("job-failed", process_failed_message),
    )
