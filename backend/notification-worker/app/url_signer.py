"""Generate signed Front Door URL for output video delivery."""
import hashlib
import hmac
import time
from app.config import settings


def generate_signed_url(output_url: str, ttl_seconds: int = 36000) -> str:
    """Generate an HMAC-SHA256 signed Front Door URL."""
    if not settings.front_door_endpoint or not settings.front_door_secret:
        return output_url  # Return plain URL if Front Door not configured

    try:
        parsed_path = output_url.split("/videos/", 1)[1].split("?")[0]
        blob_path = f"/videos/{parsed_path}"
    except (IndexError, ValueError):
        return output_url

    expires = int(time.time()) + ttl_seconds
    string_to_sign = f"{blob_path}\n{expires}"
    h = hmac.new(
        settings.front_door_secret.encode(),
        string_to_sign.encode(),
        hashlib.sha256,
    )
    signature = h.hexdigest()

    return f"{settings.front_door_endpoint}{blob_path}?expires={expires}&sig={signature}"
