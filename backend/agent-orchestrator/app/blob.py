"""Azure Blob Storage async client wrapper."""
from azure.storage.blob.aio import BlobServiceClient
from app.config import settings


async def upload_video(
    data: bytes,
    user_id: str,
    job_id: str,
    filename: str = "output.mp4",
) -> str:
    """Upload compiled video to Blob Storage and return the blob URL."""
    blob_path = f"{user_id}/outputs/{job_id}/{filename}"
    async with BlobServiceClient.from_connection_string(
        settings.azure_storage_connection_string
    ) as client:
        container = client.get_container_client(settings.azure_storage_container_name)
        blob = container.get_blob_client(blob_path)
        await blob.upload_blob(data, overwrite=True)
        return blob.url
