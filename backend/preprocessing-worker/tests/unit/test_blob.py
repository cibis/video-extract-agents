from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.blob import _blob_name_from_url, download_video, upload_keyframe


class TestBlobNameFromUrl:
    def test_extracts_blob_name_standard_url(self):
        url = "https://account.blob.core.windows.net/videos/original/vid-123.mp4"
        result = _blob_name_from_url(url, "videos")
        assert result == "original/vid-123.mp4"

    def test_extracts_blob_name_azurite_url(self):
        url = "http://localhost:10000/devstoreaccount1/videos/original/vid-123.mp4"
        result = _blob_name_from_url(url, "videos")
        assert result == "original/vid-123.mp4"

    def test_falls_back_to_url_if_container_not_found(self):
        url = "not-a-real-url"
        result = _blob_name_from_url(url, "videos")
        assert result == "not-a-real-url"


class TestDownloadVideo:
    async def test_downloads_and_writes_file(self, video_id, blob_url, tmp_path):
        mock_stream = AsyncMock()
        mock_stream.readall = AsyncMock(return_value=b"video-bytes")

        mock_blob_client = AsyncMock()
        mock_blob_client.download_blob = AsyncMock(return_value=mock_stream)

        mock_service_client = AsyncMock()
        mock_service_client.__aenter__ = AsyncMock(return_value=mock_service_client)
        mock_service_client.__aexit__ = AsyncMock(return_value=False)
        mock_service_client.get_blob_client = MagicMock(return_value=mock_blob_client)

        with patch("app.blob.BlobServiceClient.from_connection_string", return_value=mock_service_client), \
             patch("app.blob.tempfile.mkdtemp", return_value=str(tmp_path)), \
             patch("builtins.open", mock_open()):
            result = await download_video(blob_url, video_id)

        assert video_id in result


class TestUploadKeyframe:
    async def test_uploads_and_returns_url(self, tmp_path):
        frame_path = tmp_path / "frame_0.jpg"
        frame_path.write_bytes(b"img")

        mock_blob_client = AsyncMock()
        mock_blob_client.upload_blob = AsyncMock()
        mock_blob_client.url = "https://account.blob.core.windows.net/videos/keyframes/frame_0.jpg"

        mock_service_client = AsyncMock()
        mock_service_client.__aenter__ = AsyncMock(return_value=mock_service_client)
        mock_service_client.__aexit__ = AsyncMock(return_value=False)
        mock_service_client.get_blob_client = MagicMock(return_value=mock_blob_client)

        with patch("app.blob.BlobServiceClient.from_connection_string", return_value=mock_service_client), \
             patch("builtins.open", mock_open(read_data=b"img")):
            url = await upload_keyframe(str(frame_path), "videos/keyframes/frame_0.jpg")

        assert url == mock_blob_client.url
        mock_blob_client.upload_blob.assert_called_once()
