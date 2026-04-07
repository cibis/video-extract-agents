import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.consumer import (
    JOB_COMPLETED_QUEUE,
    JOB_FAILED_QUEUE,
    handle_completed,
    handle_failed,
)


class TestHandleCompleted:
    async def test_sends_email_when_user_found(self, job_id, user_id, output_url):
        now = datetime.now(timezone.utc)
        job_details = {"prompt": "Extract jumps", "created_at": now, "completed_at": now}

        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value="user@example.com"), \
             patch("app.consumer.fetch_job_details", new_callable=AsyncMock, return_value=job_details), \
             patch("app.consumer.send_completion_email", new_callable=AsyncMock) as mock_send:
            await handle_completed({"job_id": job_id, "user_id": user_id, "output_url": output_url})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["recipient_email"] == "user@example.com"
        assert call_kwargs["job_id"] == job_id

    async def test_skips_email_when_user_not_found(self, job_id, user_id, output_url):
        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value=None), \
             patch("app.consumer.send_completion_email", new_callable=AsyncMock) as mock_send:
            await handle_completed({"job_id": job_id, "user_id": user_id, "output_url": output_url})

        mock_send.assert_not_called()

    async def test_calculates_duration_from_timestamps(self, job_id, user_id, output_url):
        created = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        completed = datetime(2026, 1, 1, 12, 1, 30, tzinfo=timezone.utc)  # 90 seconds later
        job_details = {"prompt": "Extract", "created_at": created, "completed_at": completed}

        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value="u@e.com"), \
             patch("app.consumer.fetch_job_details", new_callable=AsyncMock, return_value=job_details), \
             patch("app.consumer.send_completion_email", new_callable=AsyncMock) as mock_send:
            await handle_completed({"job_id": job_id, "user_id": user_id, "output_url": output_url})

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["processing_duration_seconds"] == pytest.approx(90.0)


class TestHandleFailed:
    async def test_sends_failure_email_when_user_found(self, job_id, user_id):
        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value="user@example.com"), \
             patch("app.consumer.send_failure_email", new_callable=AsyncMock) as mock_send:
            await handle_failed({"job_id": job_id, "user_id": user_id, "error": "Timeout"})

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["error"] == "Timeout"

    async def test_skips_email_when_user_not_found(self, job_id, user_id):
        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value=None), \
             patch("app.consumer.send_failure_email", new_callable=AsyncMock) as mock_send:
            await handle_failed({"job_id": job_id, "user_id": user_id, "error": "OOM"})

        mock_send.assert_not_called()

    async def test_defaults_error_to_unknown(self, job_id, user_id):
        with patch("app.consumer.fetch_user_email", new_callable=AsyncMock, return_value="u@e.com"), \
             patch("app.consumer.send_failure_email", new_callable=AsyncMock) as mock_send:
            # No "error" key in payload
            await handle_failed({"job_id": job_id, "user_id": user_id})

        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["error"] == "Unknown error"
