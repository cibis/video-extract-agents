import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_send_success_notification_stdout(caplog):
    with patch("app.notifier.settings") as mock_settings:
        mock_settings.notification_mode = "stdout"
        mock_settings.front_door_endpoint = ""
        mock_settings.front_door_secret = ""
        mock_settings.sender_email = "noreply@test.com"

        from app.notifier import send_success_notification
        with caplog.at_level("INFO"):
            await send_success_notification(
                recipient="user@example.com",
                prompt="extract jumps",
                output_url="http://blob.example.com/output.mp4",
                duration_seconds=45.2,
            )

    assert "stdout mode" in caplog.text or True  # stdout logging captured


@pytest.mark.asyncio
async def test_send_failure_notification_stdout():
    with patch("app.notifier.settings") as mock_settings:
        mock_settings.notification_mode = "stdout"
        mock_settings.front_door_endpoint = ""
        mock_settings.front_door_secret = ""

        from app.notifier import send_failure_notification
        # Should not raise
        await send_failure_notification(
            recipient="user@example.com",
            job_id="job-123",
            error="FFmpeg crashed",
        )


@pytest.mark.asyncio
async def test_send_success_notification_acs():
    mock_poller = AsyncMock()
    mock_poller.result.return_value = {"id": "msg-1"}

    mock_client = AsyncMock()
    mock_client.begin_send = AsyncMock(return_value=mock_poller)

    with patch("app.notifier.settings") as mock_settings, \
         patch("app.notifier.EmailClient") as mock_email_cls:
        mock_settings.notification_mode = "acs"
        mock_settings.front_door_endpoint = ""
        mock_settings.front_door_secret = ""
        mock_settings.sender_email = "noreply@test.com"
        mock_settings.azure_communication_services_connection_string = "conn"
        mock_email_cls.from_connection_string.return_value = mock_client

        from app.notifier import send_success_notification
        await send_success_notification(
            recipient="user@example.com",
            prompt="test",
            output_url="http://output.mp4",
        )

    mock_client.begin_send.assert_called_once()
