"""
E2E test: transcribe_audio (Whisper, local) pipeline.

Exercises the full pipeline for audio-based extraction:
  upload → preprocess → planner selects transcribe_audio → FFmpeg audio
  extraction → Whisper transcription → segment assembly → clip extraction.

No API key required — Whisper runs locally inside the mcp-server-analysis
container.

Note on expected results:
  The test video contains a 440 Hz sine tone. Whisper will produce a sparse
  or empty transcript (no recognisable speech). The test accepts EITHER:
    - A completed job with output_url (if Whisper finds any segment), OR
    - A completed job with no_matching_segments.
  Both outcomes confirm the pipeline executed without crashing.
"""
import pytest

from tests.e2e import video_factory
from tests.e2e.helpers import (
    assert_job_succeeded,
    assert_tool_invoked,
    create_test_session,
    submit_job,
    upload_video,
    wait_for_indexed,
    wait_for_job,
)


def test_transcribe_audio_pipeline(request, tmp_path, api_gateway_url, http_client, auth_headers):
    """
    Full pipeline test for the transcribe_audio tool (local Whisper).

    1. Generate a video with a continuous 440 Hz sine audio track.
    2. Create a session and upload the video.
    3. Wait for the preprocessing worker to index keyframes.
    4. Submit a job with a prompt requesting audio/speech-based extraction.
    5. Poll until the job completes.
    6. Assert the job succeeded (sparse/empty transcript is acceptable).
    7. Assert transcribe_audio was invoked via job logs.
    """
    # 1. Generate video with audio — sine tone triggers the audio pipeline
    video_path = str(tmp_path / "audio.mp4")
    video_factory.make_audio_video(video_path)

    # 2. Create session + upload
    session_id = create_test_session(http_client, api_gateway_url, auth_headers)

    video_id, _ = upload_video(
        http_client, api_gateway_url, auth_headers, session_id, video_path
    )

    # 3. Wait for preprocessing
    wait_for_indexed(http_client, api_gateway_url, auth_headers, session_id)

    # 4. Submit job — audio/speech language routes to transcribe_audio
    job = submit_job(
        http_client, api_gateway_url, auth_headers,
        video_id=video_id, session_id=session_id,
        prompt="Extract segments based on speech and audio content in this video",
        test_name=request.node.nodeid,
    )
    job_id = job["jobId"]

    # 5. Poll to completion — Whisper model load can take ~30s on first call
    job = wait_for_job(http_client, api_gateway_url, auth_headers, job_id, timeout=240)

    # 6. Assert pipeline succeeded (empty transcript → no_matching_segments is valid)
    assert_job_succeeded(job)

    # 7. Assert Whisper transcription tool was invoked
    assert_tool_invoked(
        http_client, api_gateway_url, auth_headers, job_id, "transcribe_audio"
    )
