"""Async Service Bus consumer for video-uploaded events."""
import asyncio
import json
import logging
import tempfile
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from app.config import settings
from app.processor import extract_keyframes
from app.blob import download_video, upload_keyframe
from app.db import store_keyframe_index, update_video_status, create_session_asset, get_app_setting

logger = logging.getLogger(__name__)


async def publish_video_indexed(payload: dict) -> None:
    async with ServiceBusClient.from_connection_string(
        settings.azure_service_bus_connection_string
    ) as client:
        sender = client.get_queue_sender("video-indexed")
        async with sender:
            await sender.send_messages(
                ServiceBusMessage(
                    body=json.dumps(payload),
                    content_type="application/json",
                )
            )


async def process_video_message(message_body: dict) -> None:
    video_id = message_body.get("videoId") or message_body.get("video_id", "")
    user_id = message_body.get("userId") or message_body.get("user_id", "")
    blob_url = message_body.get("blobUrl") or message_body.get("blob_url", "")
    session_id = message_body.get("sessionId") or message_body.get("session_id")

    if not video_id or not blob_url:
        logger.error("Invalid video-uploaded message: %s", message_body)
        return

    fps = float(await get_app_setting("keyframe_fps", "1.5"))
    scene_threshold = float(await get_app_setting("keyframe_scene_threshold", "0.2"))
    logger.info("Processing video %s for user %s (fps=%.2f, scene_threshold=%.2f)",
                video_id, user_id, fps, scene_threshold)

    with tempfile.TemporaryDirectory() as tmpdir:
        local_video = f"{tmpdir}/video.mp4"

        try:
            await download_video(blob_url, local_video)
            raw_frames = await extract_keyframes(local_video, tmpdir, fps=fps, scene_threshold=scene_threshold)

            keyframes_with_urls = []
            for frame in raw_frames:
                frame_url = await upload_keyframe(
                    frame["local_path"], video_id, user_id, frame["frame_index"]
                )
                keyframes_with_urls.append({
                    "frame_index": frame["frame_index"],
                    "frame_url": frame_url,
                    "timestamp_seconds": frame["timestamp_seconds"],
                })

            await store_keyframe_index(video_id, keyframes_with_urls)
            await update_video_status(video_id, "indexed")

            if session_id:
                await create_session_asset(
                    session_id=session_id,
                    asset_type="uploaded_video",
                    blob_url=blob_url,
                    source_id=video_id,
                    content_type="video/mp4",
                    label=f"video:{video_id}",
                )

            await publish_video_indexed({
                "videoId": video_id,
                "userId": user_id,
                "sessionId": session_id,
                "keyframeCount": len(keyframes_with_urls),
                "keyframeUrls": [kf["frame_url"] for kf in keyframes_with_urls],
            })

            logger.info("Video %s indexed with %d keyframes", video_id, len(keyframes_with_urls))

        except Exception as exc:
            logger.error("Failed to process video %s: %s", video_id, exc)
            await update_video_status(video_id, "failed")
            raise


_BACKOFF_BASE = 2
_BACKOFF_MAX = 60


async def run_consumer() -> None:
    logger.info("Starting Service Bus consumer for queue: video-uploaded")
    attempt = 0
    while True:
        try:
            async with ServiceBusClient.from_connection_string(
                settings.azure_service_bus_connection_string
            ) as client:
                receiver = client.get_queue_receiver(queue_name="video-uploaded", max_wait_time=5)
                async with receiver:
                    attempt = 0
                    logger.info("Connected to Service Bus queue: video-uploaded")
                    while True:
                        messages = await receiver.receive_messages(max_message_count=1, max_wait_time=5)
                        for msg in messages:
                            try:
                                body = json.loads(str(msg))
                                await process_video_message(body)
                                await receiver.complete_message(msg)
                            except Exception as exc:
                                logger.error("Message processing failed: %s", exc)
                                await receiver.abandon_message(msg)
                        await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Consumer task cancelled; shutting down")
            raise
        except Exception as exc:
            wait = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)
            logger.warning(
                "Service Bus connection failed (attempt %d), retrying in %ss: %s",
                attempt + 1, wait, exc,
            )
            attempt += 1
            await asyncio.sleep(wait)
