"""Unit tests for analyze_scene error surfacing in summary."""
import pytest


_FRAMES_ASSET_URL = "http://blob/scene_frames.json"
_RESULT_ASSET_URL = "http://blob/scene_result.json"

_FRAMES_DATA = {
    "frames": [
        {"url": "http://blob/frame0.jpg", "timestamp_seconds": 0.0},
        {"url": "http://blob/frame1.jpg", "timestamp_seconds": 1.0},
    ]
}

_GOOD_BATCH_RESULTS = [
    {"description": "A sunny beach", "objects": ["sand", "sea"], "activities": ["walking"], "setting": "outdoor", "mood": "calm"},
    {"description": "A busy street", "objects": ["car", "person"], "activities": ["driving"], "setting": "outdoor", "mood": "busy"},
]


@pytest.mark.asyncio
async def test_errors_array_empty_when_all_frames_succeed(mocker):
    mocker.patch(
        "app.tools.analyze_scene.read_generated_asset",
        return_value=_FRAMES_DATA,
    )
    mock_write = mocker.patch(
        "app.tools.analyze_scene.write_generated_asset",
        return_value=_RESULT_ASSET_URL,
    )

    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=_GOOD_BATCH_RESULTS)
    mocker.patch(
        "app.tools.analyze_scene.get_model_client",
        return_value=mock_client,
    )

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 2,
            "resolution": (224, 224),
            "max_frames_allowed": 20,
            "task_type": task_type,
            "fetch_errors": {},
        }
        await callback(["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.analyze_scene.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.analyze_scene import analyze_scene

    result = await analyze_scene({
        "frames_asset": _FRAMES_ASSET_URL,
        "job_id": "job-1",
    })

    summary = result["summary"]
    assert summary["frames_analysed"] == 2
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
    assert batch["resolution"] == [224, 224]
    assert batch["frame_range"] == [0, 2]


@pytest.mark.asyncio
async def test_errors_array_populated_on_no_response(mocker):
    """When call_vision_batch returns no_response entries, they appear in errors."""
    mocker.patch(
        "app.tools.analyze_scene.read_generated_asset",
        return_value=_FRAMES_DATA,
    )
    mock_write = mocker.patch(
        "app.tools.analyze_scene.write_generated_asset",
        return_value=_RESULT_ASSET_URL,
    )

    no_response_results = [
        {"error": "no_response", "error_detail": "JSON parse failed: ValueError: bad json", "traceback": "Traceback..."},
        {"error": "no_response", "error_detail": "JSON parse failed: ValueError: bad json", "traceback": "Traceback..."},
    ]
    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=no_response_results)
    mocker.patch("app.tools.analyze_scene.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 2,
            "resolution": (224, 224),
            "max_frames_allowed": 20,
            "task_type": task_type,
            "fetch_errors": {},
        }
        await callback(["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.analyze_scene.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.analyze_scene import analyze_scene

    result = await analyze_scene({"frames_asset": _FRAMES_ASSET_URL, "job_id": "job-1"})

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
    """Frames with no URL carry fetch error detail from metadata into the errors array."""
    frames_with_missing_url = {
        "frames": [
            {"url": "", "timestamp_seconds": 0.0},  # missing URL
            {"url": "http://blob/frame1.jpg", "timestamp_seconds": 1.0},
        ]
    }
    mocker.patch("app.tools.analyze_scene.read_generated_asset", return_value=frames_with_missing_url)
    mock_write = mocker.patch("app.tools.analyze_scene.write_generated_asset", return_value=_RESULT_ASSET_URL)

    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=[_GOOD_BATCH_RESULTS[1]])
    mocker.patch("app.tools.analyze_scene.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        metadata = {
            "batch_index": 0,
            "start_frame": 0,
            "end_frame": 2,
            "frames_in_batch": 1,
            "resolution": (224, 224),
            "max_frames_allowed": 20,
            "task_type": task_type,
            "fetch_errors": {"": "ConnectionError: could not connect\nTraceback..."},
        }
        await callback(["data:image/jpeg;base64,def"], metadata)

    mocker.patch("app.tools.analyze_scene.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.analyze_scene import analyze_scene

    result = await analyze_scene({"frames_asset": _FRAMES_ASSET_URL, "job_id": "job-1"})

    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 1
    err = asset_data["errors"][0]
    assert err["error_type"] == "no_url"
    assert "ConnectionError" in err["error_detail"]
    assert err["frame_index"] == 0


@pytest.mark.asyncio
async def test_errors_array_no_url_without_fetch_error(mocker):
    """Frames with no URL but no fetch error detail get no_url without error_detail key."""
    frames_no_url = {
        "frames": [
            {"url": "", "timestamp_seconds": 0.0},
        ]
    }
    mocker.patch("app.tools.analyze_scene.read_generated_asset", return_value=frames_no_url)
    mock_write = mocker.patch("app.tools.analyze_scene.write_generated_asset", return_value=_RESULT_ASSET_URL)

    mock_client = mocker.AsyncMock()
    mock_client.model_id = "anthropic/claude-opus-4-6"
    mock_client.call_vision_batch = mocker.AsyncMock(return_value=[])
    mocker.patch("app.tools.analyze_scene.get_model_client", return_value=mock_client)

    async def fake_batches(image_urls, model_name, callback, task_type):
        # Empty data_uris — all frames dropped, batch skipped; callback never called
        pass

    mocker.patch("app.tools.analyze_scene.process_frames_in_batches", side_effect=fake_batches)

    from app.tools.analyze_scene import analyze_scene

    result = await analyze_scene({"frames_asset": _FRAMES_ASSET_URL, "job_id": "job-1"})

    # Callback was never called, so per_frame_results is empty; no errors
    asset_data = mock_write.call_args.kwargs["data"]
    assert asset_data["frames_with_errors"] == 0
    assert asset_data["errors"] == []
