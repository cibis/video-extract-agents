import pytest
from unittest.mock import patch


def test_generate_signed_url_with_front_door():
    with patch("app.url_signer.settings") as mock_settings:
        mock_settings.front_door_endpoint = "https://cdn.example.com"
        mock_settings.front_door_secret = "supersecret"

        from app.url_signer import generate_signed_url
        result = generate_signed_url("http://blob.example.com/videos/user/outputs/job-1/output.mp4")

    assert "cdn.example.com" in result
    assert "expires=" in result
    assert "sig=" in result


def test_generate_signed_url_returns_plain_without_config():
    with patch("app.url_signer.settings") as mock_settings:
        mock_settings.front_door_endpoint = ""
        mock_settings.front_door_secret = ""

        from app.url_signer import generate_signed_url
        url = "http://blob.example.com/output.mp4"
        result = generate_signed_url(url)

    assert result == url
