from unittest.mock import AsyncMock, patch

import pytest

from app.db import store_keyframe_index


class TestStoreKeyframeIndex:
    async def test_inserts_records(self, video_id):
        frames = [
            {"frame_index": 0, "frame_url": "https://blob/frame_0.jpg", "timestamp_seconds": 0.0},
            {"frame_index": 1, "frame_url": "https://blob/frame_1.jpg", "timestamp_seconds": 1.0},
        ]

        mock_conn = AsyncMock()
        mock_tx = AsyncMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            await store_keyframe_index(video_id, frames)

        mock_conn.execute.assert_called_once_with(
            "DELETE FROM video_keyframe_index WHERE video_id = $1",
            video_id,
        )
        mock_conn.executemany.assert_called_once()
        args = mock_conn.executemany.call_args[0]
        assert len(args[1]) == 2

    async def test_closes_connection_on_success(self, video_id):
        mock_conn = AsyncMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            await store_keyframe_index(video_id, [])

        mock_conn.close.assert_called_once()

    async def test_closes_connection_on_error(self, video_id):
        mock_conn = AsyncMock()
        mock_conn.transaction.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
        mock_conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute.side_effect = Exception("DB error")

        with patch("app.db.asyncpg.connect", new_callable=AsyncMock, return_value=mock_conn):
            with pytest.raises(Exception, match="DB error"):
                await store_keyframe_index(video_id, [])

        mock_conn.close.assert_called_once()
