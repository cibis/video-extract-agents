"""Unit tests for detect_objects_vision error surfacing in result asset."""
import pytest


_FRAMES_ASSET_URL = "http://blob/frames.json"
_RESULT_ASSET_URL = "http://blob/detections_result.json"

_FRAMES_DATA = {
    "frames": [
        {"url": "http://blob/frame0.jpg", "timestamp_seconds": 0.0},
        {"url": "http://blob/frame1.jpg", "timestamp_seconds": 1.0},
    ]
}

_GOOD_BATCH_RESULTS = [
    {"detections": [{"object": "kite", "present": True, "confidence": 0.9, "location_description": "upper left", "bbox_rough": [10, 5, 30, 25]}]},
    {"detections": [{"object": "kite", "present": False, "confidence": 0.1, "location_description": "", "bbox_rough": []}]},
]


@pytest.mark.asyncio
async def test_errors_array_empty_when_all_frames_succeed(mocker):
    mocker.patch("app.tools.detect_objects_vision.read_generated_asset", return_value=_FRAMES_DATA)
    mock_write = mocker.patch("app.tools.detect_objects_vision.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects_vision.aggregate_detections_to_segments", return_value=[])

    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=_GOOD_BATCH_RESULTS)
    mocker.patch("app.tools.detect_objects_vision.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 2,
            "resolution": (192, 192),
            "max_frames_allowed": 25,
            "task_type": task_type,
            "fetch_errors": {},
        }
        await callback(["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.detect_objects_vision.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.detect_objects_vision import detect_objects_vision

    result = await detect_objects_vision({
        "frames_asset": _FRAMES_ASSET_URL,
        "object_descriptions": ["kite"],
        "job_id": "job-1",
    })

    summary = result["summary"]
    assert summary["frames_analysed"] == 2
    assert summary["frames_with_detections"] == 1
    assert "errors" not in summary
    assert "batches" not in summary

    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 0
    assert asset_data["errors"] == []
    assert asset_data["total_batches"] == 1
    assert len(asset_data["batches"]) == 1
    batch = asset_data["batches"][0]
    assert batch["batch_index"] == 0
    assert batch["frames_in_batch"] == 2
    assert batch["resolution"] == [192, 192]
    assert batch["frame_range"] == [0, 2]


@pytest.mark.asyncio
async def test_errors_array_populated_on_no_response(mocker):
    """When call_vision_batch returns no_response entries, they appear in result asset errors."""
    mocker.patch("app.tools.detect_objects_vision.read_generated_asset", return_value=_FRAMES_DATA)
    mock_write = mocker.patch("app.tools.detect_objects_vision.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects_vision.aggregate_detections_to_segments", return_value=[])

    no_response_results = [
        {"detections": [], "error": "no_response", "error_detail": "JSON parse failed: JSONDecodeError: ...", "traceback": "Traceback (most recent call last):\n  ..."},
        {"detections": [], "error": "no_response", "error_detail": "JSON parse failed: JSONDecodeError: ...", "traceback": "Traceback (most recent call last):\n  ..."},
    ]
    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=no_response_results)
    mocker.patch("app.tools.detect_objects_vision.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 2,
            "resolution": (192, 192),
            "max_frames_allowed": 25,
            "task_type": task_type,
            "fetch_errors": {},
        }
        await callback(["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.detect_objects_vision.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.detect_objects_vision import detect_objects_vision

    result = await detect_objects_vision({
        "frames_asset": _FRAMES_ASSET_URL,
        "object_descriptions": ["kite"],
        "job_id": "job-1",
    })

    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 2
    assert len(asset_data["errors"]) == 2
    err = asset_data["errors"][0]
    assert err["error_type"] == "no_response"
    assert "JSON parse failed" in err["error_detail"]
    assert "Traceback" in err["traceback"]
    assert err["frame_index"] == 0
    assert err["timestamp_seconds"] == 0.0


@pytest.mark.asyncio
async def test_errors_array_populated_on_no_url_with_fetch_error(mocker):
    """Frames with missing URL carry fetch error detail into the result asset errors array."""
    frames_with_missing_url = {
        "frames": [
            {"url": "", "timestamp_seconds": 0.0},
            {"url": "http://blob/frame1.jpg", "timestamp_seconds": 1.0},
        ]
    }
    mocker.patch("app.tools.detect_objects_vision.read_generated_asset", return_value=frames_with_missing_url)
    mock_write = mocker.patch("app.tools.detect_objects_vision.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects_vision.aggregate_detections_to_segments", return_value=[])

    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=[_GOOD_BATCH_RESULTS[1]])
    mocker.patch("app.tools.detect_objects_vision.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 1,
            "resolution": (192, 192),
            "max_frames_allowed": 25,
            "task_type": task_type,
            "fetch_errors": {"": "TimeoutError: request timed out\nTraceback..."},
        }
        await callback(["data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.detect_objects_vision.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.detect_objects_vision import detect_objects_vision

    result = await detect_objects_vision({
        "frames_asset": _FRAMES_ASSET_URL,
        "object_descriptions": ["kite"],
        "job_id": "job-1",
    })

    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 1
    err = asset_data["errors"][0]
    assert err["error_type"] == "no_url"
    assert "TimeoutError" in err["error_detail"]
    assert err["frame_index"] == 0


@pytest.mark.asyncio
async def test_errors_array_no_response_without_parse_error(mocker):
    """Partial model response (fewer items than frames) → no_response without traceback."""
    mocker.patch("app.tools.detect_objects_vision.read_generated_asset", return_value=_FRAMES_DATA)
    mock_write = mocker.patch("app.tools.detect_objects_vision.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects_vision.aggregate_detections_to_segments", return_value=[])

    # Only 1 result for 2 frames — model returned fewer items, no parse exception
    partial_results = [_GOOD_BATCH_RESULTS[0], {"detections": [], "error": "no_response"}]
    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=partial_results)
    mocker.patch("app.tools.detect_objects_vision.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 2,
            "resolution": (192, 192),
            "max_frames_allowed": 25,
            "task_type": task_type,
            "fetch_errors": {},
        }
        await callback(["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.detect_objects_vision.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.detect_objects_vision import detect_objects_vision

    result = await detect_objects_vision({
        "frames_asset": _FRAMES_ASSET_URL,
        "object_descriptions": ["kite"],
        "job_id": "job-1",
    })

    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 1
    err = asset_data["errors"][0]
    assert err["error_type"] == "no_response"
    assert "error_detail" not in err
    assert "traceback" not in err
    assert err["frame_index"] == 1
