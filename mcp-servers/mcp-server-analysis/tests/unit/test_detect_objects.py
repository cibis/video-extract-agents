"""Unit tests for detect_objects (YOLO-World open-vocabulary detection)."""
import sys
import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch


_FRAMES_ASSET_URL = "http://blob/frames.json"
_RESULT_ASSET_URL = "http://blob/detections.json"

_FRAMES_DATA = {
    "frames": [
        {"url": "http://blob/frame0.jpg", "timestamp_seconds": 0.0},
        {"url": "http://blob/frame1.jpg", "timestamp_seconds": 1.0},
    ]
}


def _make_mock_model(class_name: str = "water", confidence: float = 0.82,
                     bbox: list | None = None) -> MagicMock:
    """Return a MagicMock YOLOWorld model that yields one detection per call."""
    if bbox is None:
        bbox = [10.0, 20.0, 100.0, 80.0]

    mock_box = MagicMock()
    mock_box.cls.__int__ = lambda s: 0
    mock_box.conf.__float__ = lambda s: confidence
    mock_box.xyxy = [MagicMock()]
    mock_box.xyxy[0].tolist.return_value = bbox

    mock_result = MagicMock()
    mock_result.boxes = [mock_box]

    mock_model = MagicMock()
    mock_model.names = [class_name]
    mock_model.return_value = [mock_result]
    return mock_model


def _make_empty_mock_model() -> MagicMock:
    """Return a MagicMock YOLOWorld model that yields no detections."""
    mock_result = MagicMock()
    mock_result.boxes = []
    mock_model = MagicMock()
    mock_model.names = []
    mock_model.return_value = [mock_result]
    return mock_model


def _fake_http_client():
    """Return a mock httpx.AsyncClient that returns a minimal JPEG response."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.content = b"\xff\xd8\xff"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ── detection tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_objects_no_detections(mocker):
    """No detections → empty segments, zero total_detections."""
    mocker.patch("app.tools.detect_objects.read_generated_asset", return_value=_FRAMES_DATA)
    mocker.patch("app.tools.detect_objects.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects.aggregate_detections_to_segments", return_value=[])

    with (
        patch("app.tools.detect_objects._model", _make_empty_mock_model()),
        patch("app.tools.detect_objects.httpx.AsyncClient", return_value=_fake_http_client()),
        patch("cv2.imdecode", return_value=np.zeros((100, 100, 3), dtype=np.uint8)),
    ):
        from app.tools.detect_objects import detect_objects
        result = await detect_objects({
            "frames_asset": _FRAMES_ASSET_URL,
            "object_classes": ["water"],
            "job_id": "job-1",
        })

    assert result["summary"]["total_detections"] == 0
    assert result["summary"]["segments"] == []
    assert result["summary"]["classes_detected"] == []


@pytest.mark.asyncio
async def test_detect_objects_with_detection(mocker):
    """Mock YOLO-World returning a water detection → appears in result asset."""
    mocker.patch("app.tools.detect_objects.read_generated_asset", return_value=_FRAMES_DATA)
    mock_write = mocker.patch(
        "app.tools.detect_objects.write_generated_asset", return_value=_RESULT_ASSET_URL
    )
    mocker.patch(
        "app.tools.detect_objects.aggregate_detections_to_segments",
        return_value=[{"start_seconds": 0.0, "end_seconds": 1.0, "classes": ["water"]}],
    )

    with (
        patch("app.tools.detect_objects._model", _make_mock_model("water", 0.82)),
        patch("app.tools.detect_objects.httpx.AsyncClient", return_value=_fake_http_client()),
        patch("cv2.imdecode", return_value=np.zeros((100, 100, 3), dtype=np.uint8)),
    ):
        from app.tools.detect_objects import detect_objects
        result = await detect_objects({
            "frames_asset": _FRAMES_ASSET_URL,
            "object_classes": ["water"],
            "job_id": "job-1",
        })

    assert result["summary"]["total_detections"] == 2  # both frames detected
    asset_data = mock_write.call_args.kwargs["data"]
    assert len(asset_data["detections"]) == 2
    det = asset_data["detections"][0]
    assert det["objects"][0]["class"] == "water"
    assert det["objects"][0]["confidence"] == pytest.approx(0.82, abs=0.001)
    assert det["objects"][0]["bbox"] == {"x": 10, "y": 20, "width": 90, "height": 60}


@pytest.mark.asyncio
async def test_detect_objects_set_classes_called_with_requested_classes(mocker):
    """set_classes must be called on the model with the exact requested class list."""
    mocker.patch("app.tools.detect_objects.read_generated_asset", return_value={
        "frames": [{"url": "http://blob/f.jpg", "timestamp_seconds": 0.0}]
    })
    mocker.patch("app.tools.detect_objects.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects.aggregate_detections_to_segments", return_value=[])

    mock_model = _make_empty_mock_model()

    with (
        patch("app.tools.detect_objects._model", mock_model),
        patch("app.tools.detect_objects.httpx.AsyncClient", return_value=_fake_http_client()),
        patch("cv2.imdecode", return_value=np.zeros((100, 100, 3), dtype=np.uint8)),
    ):
        from app.tools.detect_objects import detect_objects
        await detect_objects({
            "frames_asset": _FRAMES_ASSET_URL,
            "object_classes": ["water", "kite"],
            "job_id": "job-1",
        })

    mock_model.set_classes.assert_called_once_with(["water", "kite"])


@pytest.mark.asyncio
async def test_detect_objects_deduplicates_classes(mocker):
    """Duplicate/case-variant entries in object_classes are deduplicated before set_classes."""
    mocker.patch("app.tools.detect_objects.read_generated_asset", return_value={
        "frames": [{"url": "http://blob/f.jpg", "timestamp_seconds": 0.0}]
    })
    mocker.patch("app.tools.detect_objects.write_generated_asset", return_value=_RESULT_ASSET_URL)
    mocker.patch("app.tools.detect_objects.aggregate_detections_to_segments", return_value=[])

    mock_model = _make_empty_mock_model()

    with (
        patch("app.tools.detect_objects._model", mock_model),
        patch("app.tools.detect_objects.httpx.AsyncClient", return_value=_fake_http_client()),
        patch("cv2.imdecode", return_value=np.zeros((100, 100, 3), dtype=np.uint8)),
    ):
        from app.tools.detect_objects import detect_objects
        await detect_objects({
            "frames_asset": _FRAMES_ASSET_URL,
            "object_classes": ["Water", "water", "WATER"],
            "job_id": "job-1",
        })

    mock_model.set_classes.assert_called_once_with(["water"])


@pytest.mark.asyncio
async def test_detect_objects_missing_cv2_raises():
    """ImportError for cv2 surfaces as RuntimeError with a useful message."""
    sys.modules.pop("app.tools.detect_objects", None)
    with patch.dict(sys.modules, {"cv2": None, "numpy": None}):
        from app.tools.detect_objects import detect_objects
        with (
            patch(
                "app.tools.detect_objects.read_generated_asset",
                new=AsyncMock(return_value={"frames": [{"url": "http://blob/f.jpg", "timestamp_seconds": 0.0}]}),
            ),
            pytest.raises(RuntimeError, match="opencv-python"),
        ):
            await detect_objects({
                "frames_asset": _FRAMES_ASSET_URL,
                "object_classes": ["person"],
                "job_id": "job-1",
            })
    sys.modules.pop("app.tools.detect_objects", None)
