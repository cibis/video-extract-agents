from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.db import fetch_job_details, fetch_user_email


class TestFetchUserEmail:
    async def test_returns_email_when_user_found(self, user_id):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={"email": "user@example.com"})

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            result = await fetch_user_email(user_id)

        assert result == "user@example.com"
        mock_conn.close.assert_called_once()

    async def test_returns_none_when_user_not_found(self, user_id):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            result = await fetch_user_email(user_id)

        assert result is None

    async def test_closes_connection_on_error(self, user_id):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(side_effect=Exception("DB down"))

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            with pytest.raises(Exception, match="DB down"):
                await fetch_user_email(user_id)

        mock_conn.close.assert_called_once()


class TestFetchJobDetails:
    async def test_returns_job_dict_when_found(self, job_id):
        now = datetime.now(timezone.utc)
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(
            return_value={"prompt": "Extract jumps", "created_at": now, "completed_at": now}
        )

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            result = await fetch_job_details(job_id)

        assert result["prompt"] == "Extract jumps"
        mock_conn.close.assert_called_once()

    async def test_returns_none_when_job_not_found(self, job_id):
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            result = await fetch_job_details(job_id)

        assert result is None
