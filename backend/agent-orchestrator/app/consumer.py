"""Azure Service Bus consumer for job-queued events."""
import asyncio
import json
import logging
from azure.servicebus.aio import ServiceBusClient
from app.config import settings
from app.crew import run_crew
from app.db import get_job, update_job_status

logger = logging.getLogger(__name__)


async def process_job_message(message_body: dict) -> None:
    job_id = message_body.get("jobId") or message_body.get("job_id", "")
    user_id = message_body.get("userId") or message_body.get("user_id", "")
    prompt = message_body.get("prompt", "")
    # Support both single videoUrl and videoIds array
    video_url = message_body.get("videoUrl") or message_body.get("video_url", "")
    video_ids = message_body.get("videoIds") or message_body.get("video_ids") or []
    session_id = message_body.get("sessionId") or message_body.get("session_id") or None
    parent_job_id = message_body.get("parentJobId") or message_body.get("parent_job_id") or None

    if not job_id:
        logger.error("Received job message without jobId: %s", message_body)
        return

    # Fetch full job from DB
    job = await get_job(job_id)
    if not job:
        logger.error("Job not found in DB: %s", job_id)
        return

    # Idempotency: skip jobs already in a terminal state (handles Service Bus redelivery)
    if job.get("status") in ("completed", "failed"):
        logger.info(
            "Job %s already in terminal state '%s' — skipping redelivered message",
            job_id, job.get("status"),
        )
        return

    prompt = prompt or job.get("prompt", "")
    session_id = session_id or (str(job["session_id"]) if job.get("session_id") else None)
    parent_job_id = parent_job_id or (str(job["parent_job_id"]) if job.get("parent_job_id") else None)

    # Build the video URL list from DB job fields (backward compat + new array field)
    db_video_ids = job.get("video_ids") or []
    if not video_ids and db_video_ids:
        video_ids = [str(v) for v in db_video_ids]

    # If we have video_ids UUIDs we need to resolve URLs; for now fall back to video_url
    if not video_ids and video_url:
        video_urls = [video_url]
    elif video_ids and not video_url:
        # video_ids here are actual URLs passed through the message
        video_urls = video_ids
    else:
        video_urls = [video_url] if video_url else []

    await update_job_status(job_id, "processing")

    try:
        output_url = await run_crew(
            prompt=prompt,
            video_urls=video_urls,
            job_id=job_id,
            user_id=user_id,
            session_id=session_id,
            parent_job_id=parent_job_id,
        )
        await update_job_status(job_id, "completed", output_url=output_url)
        logger.info("Job %s completed: %s", job_id, output_url)
    except Exception as exc:
        logger.error("Job %s failed: %s", job_id, exc)
        await update_job_status(job_id, "failed", error=str(exc))


async def _renew_lock(receiver, msg, interval: int = 45) -> None:
    """Renew a Service Bus message lock every `interval` seconds.

    Keeps long-running jobs (frontier model calls, multi-step crews) from
    losing their lock on the Standard-tier emitter (default lock = 60 s).
    Cancelled by the caller as soon as processing finishes.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            await receiver.renew_message_lock(msg)
            logger.debug("Renewed SB message lock for %s", getattr(msg, "message_id", "?"))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Failed to renew SB message lock (non-fatal): %s", exc)
            break


async def run_consumer() -> None:
    """Consume job-queued messages indefinitely, reconnecting on failure."""
    backoff = 2
    while True:
        try:
            logger.info("Starting Service Bus consumer for queue: job-queued")
            async with ServiceBusClient.from_connection_string(
                settings.azure_service_bus_connection_string
            ) as client:
                receiver = client.get_queue_receiver(queue_name="job-queued", max_wait_time=60*10)
                async with receiver:
                    backoff = 2  # reset on successful connection
                    while True:
                        messages = await receiver.receive_messages(max_message_count=1, max_wait_time=60*10)
                        for msg in messages:
                            renewer = asyncio.create_task(_renew_lock(receiver, msg))
                            try:
                                body = json.loads(str(msg))
                                await process_job_message(body)
                                await receiver.complete_message(msg)
                            except Exception as exc:
                                logger.error("Failed to process message: %s", exc)
                                await receiver.abandon_message(msg)
                            finally:
                                renewer.cancel()
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Service Bus consumer disconnected (%s). Retrying in %ds...", exc, backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_consumer())
