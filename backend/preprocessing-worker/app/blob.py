"""Azure Blob Storage: download video, upload keyframes."""
import os
from pathlib import Path
from azure.storage.blob.aio import BlobServiceClient
from app.config import settings


async def download_video(blob_url: str, local_path: str) -> None:
    """Download a video blob to a local path."""
    # Extract container + blob path from URL
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        # Parse blob path from URL
        parts = blob_url.split(f"/{settings.azure_storage_container_name}/", 1)
        if len(parts) != 2:
            raise ValueError(f"Cannot parse blob path from URL: {blob_url}")
        blob_path = parts[1].split("?")[0]  # Strip SAS token if present

        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        with open(local_path, "wb") as f:
            stream = await blob_client.download_blob()
            data = await stream.readall()
            f.write(data)


async def upload_keyframe(
    local_path: str,
    video_id: str,
    user_id: str,
    frame_index: int,
) -> str:
    """Upload a keyframe image to Blob Storage and return its URL."""
    blob_path = f"{user_id}/keyframes/{video_id}/frame_{frame_index:04d}.jpg"
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        blob_client = client.get_blob_client(
            container=settings.azure_storage_container_name,
            blob=blob_path,
        )
        with open(local_path, "rb") as f:
            await blob_client.upload_blob(f, overwrite=True)
        return blob_client.url
