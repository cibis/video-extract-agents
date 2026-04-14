"""Send email notifications. Branches on NOTIFICATION_MODE."""
import logging
from app.config import settings
from app.url_signer import generate_signed_url

logger = logging.getLogger(__name__)


def _build_success_email(
    recipient: str,
    prompt: str,
    output_url: str,
    duration_seconds: float | None,
) -> dict:
    signed_url = generate_signed_url(output_url)
    duration_str = f"{duration_seconds:.1f}s" if duration_seconds else "N/A"
    body = (
        f"Your video extraction is complete!\n\n"
        f"Prompt: {prompt}\n"
        f"Processing time: {duration_str}\n\n"
        f"Download your video: {signed_url}\n\n"
        f"This link expires in 1 hour."
    )
    return {
        "to": recipient,
        "subject": "Your Video Extract is Ready",
        "body": body,
        "signed_url": signed_url,
    }


def _build_failure_email(recipient: str, job_id: str, error: str) -> dict:
    body = (
        f"Unfortunately, your video extraction job failed.\n\n"
        f"Job ID: {job_id}\n"
        f"Reason: {error}\n\n"
        f"Please try again or contact support."
    )
    return {
        "to": recipient,
        "subject": "Video Extraction Failed",
        "body": body,
    }


async def send_success_notification(
    recipient: str,
    prompt: str,
    output_url: str,
    duration_seconds: float | None = None,
) -> None:
    email = _build_success_email(recipient, prompt, output_url, duration_seconds)
    await _dispatch_email(email)


async def send_failure_notification(
    recipient: str,
    job_id: str,
    error: str,
) -> None:
    email = _build_failure_email(recipient, job_id, error)
    await _dispatch_email(email)


def _is_valid_email(address: str) -> bool:
    """Return True only when address looks like a real deliverable email.

    Rejects empty strings, None, and synthetic values such as 'dev@local'
    (no dot in the domain part) that appear in test/dev environments.
    """
    if not address or "@" not in address:
        return False
    _, domain = address.rsplit("@", 1)
    return "." in domain


async def _dispatch_email(email: dict) -> None:
    if settings.notification_mode == "stdout":
        logger.info(
            "EMAIL [stdout mode]\nTo: %s\nSubject: %s\n\n%s",
            email["to"],
            email["subject"],
            email["body"],
        )
        return

    # ACS mode — validate before calling ACS to avoid dead-lettering on
    # synthetic addresses (e.g. 'dev@local') used in CI / local dev.
    recipient = email.get("to", "")
    if not _is_valid_email(recipient):
        logger.warning(
            "Skipping ACS send — non-deliverable address %r (no dot in domain). "
            "Update the user record with a real email to receive notifications.",
            recipient,
        )
        return

    from azure.communication.email import EmailClient
    client = EmailClient.from_connection_string(
        settings.azure_communication_services_connection_string
    )
    message = {
        "senderAddress": settings.sender_email,
        "recipients": {"to": [{"address": recipient}]},
        "content": {
            "subject": email["subject"],
            "plainText": email["body"],
        },
    }
    poller = client.begin_send(message)
    result = poller.result()
    logger.info("Email sent via ACS: %s", result)
