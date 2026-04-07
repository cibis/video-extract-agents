"""Unit tests for estimate_height_above_surface."""
import math
import sys
import pytest
import numpy as np
from unittest.mock import AsyncMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_depth_map(value: float, h: int = 20, w: int = 20) -> np.ndarray:
    return np.full((h, w), value, dtype=np.float32)


def _make_frames(n: int) -> list[dict]:
    return [
        {"url": f"http://blob/frame_{i}.jpg", "timestamp_seconds": float(i)}
        for i in range(n)
    ]


# ── _compute_heights tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_height_m_always_populated():
    """Every frame record has a float height_m — never null."""
    from app.tools.estimate_height_above_surface import _compute_heights

    frames = _make_frames(3)
    depth_values = [1.0, 2.5, 0.3]

    with (
        patch(
            "app.tools.estimate_height_above_surface._download_frame",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch("cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        patch(
            "app.tools.estimate_height_above_surface._run_depth_inference",
            side_effect=[_make_depth_map(v) for v in depth_values],
        ),
    ):
        records = await _compute_heights(
            frames, frame_batch_size=10, surface_sample_pct=0.5, camera_vfov_deg=60.0
        )

    assert len(records) == 3
    for rec in records:
        assert isinstance(rec["height_m"], float)
        assert rec["height_m"] is not None


@pytest.mark.asyncio
async def test_surface_sample_region():
    """Only the bottom surface_sample_pct rows of the depth map are sampled."""
    from app.tools.estimate_height_above_surface import _compute_heights

    frames = _make_frames(1)
    # Top 10 rows = 0.0 m (sky-like, below threshold), bottom 10 rows = 1.5 m (surface).
    # _detect_horizon_frac finds row 10 as first row with median > 1.0 → horizon_frac = 0.5
    # _tilt_corrected_height(1.5, 0.5, 60) → tilt=0°, bottom_strip_angle=30° → 1.5 × sin(30°) = 0.75
    depth_map = np.zeros((20, 20), dtype=np.float32)
    depth_map[10:, :] = 1.5

    with (
        patch(
            "app.tools.estimate_height_above_surface._download_frame",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch("cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        patch(
            "app.tools.estimate_height_above_surface._run_depth_inference",
            return_value=depth_map,
        ),
    ):
        records = await _compute_heights(
            frames, frame_batch_size=10, surface_sample_pct=0.5, camera_vfov_deg=60.0
        )

    assert len(records) == 1
    # raw_depth_m = 1.5 (bottom rows), horizon_frac = 0.5, sin(30°) = 0.5 → height = 0.75
    assert records[0]["height_m"] == pytest.approx(0.75, abs=0.01)


@pytest.mark.asyncio
async def test_failed_download_skipped():
    """Frames that fail to download are skipped without crashing."""
    from app.tools.estimate_height_above_surface import _compute_heights

    frames = _make_frames(2)

    async def _side_effect(url: str) -> bytes | None:
        return None if "frame_0" in url else b"fake"

    with (
        patch(
            "app.tools.estimate_height_above_surface._download_frame",
            side_effect=_side_effect,
        ),
        patch("cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        patch(
            "app.tools.estimate_height_above_surface._run_depth_inference",
            return_value=_make_depth_map(2.0),
        ),
    ):
        records = await _compute_heights(
            frames, frame_batch_size=10, surface_sample_pct=0.2, camera_vfov_deg=60.0
        )

    assert len(records) == 1
    assert records[0]["timestamp_seconds"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_empty_frames_returns_empty():
    """Empty frames list returns empty records without crashing."""
    from app.tools.estimate_height_above_surface import _compute_heights

    records = await _compute_heights([], frame_batch_size=10, surface_sample_pct=0.2, camera_vfov_deg=60.0)
    assert records == []


@pytest.mark.asyncio
async def test_missing_opencv_raises():
    """ImportError for cv2 surfaces as RuntimeError with a useful message."""
    sys.modules.pop("app.tools.estimate_height_above_surface", None)
    with patch.dict(sys.modules, {"cv2": None}):
        from app.tools.estimate_height_above_surface import _compute_heights
        with pytest.raises(RuntimeError, match="opencv-python"):
            await _compute_heights(
                _make_frames(1), frame_batch_size=10, surface_sample_pct=0.2, camera_vfov_deg=60.0
            )
    sys.modules.pop("app.tools.estimate_height_above_surface", None)


# ── tilt correction tests ─────────────────────────────────────────────────────

def test_detect_horizon_frac_finds_sky_ground_boundary():
    """Depth map with top half near-zero (sky) and bottom half 5m (ground)
    → horizon_frac ≈ 0.5 (first row at or past the midpoint)."""
    from app.tools.estimate_height_above_surface import _detect_horizon_frac

    depth_map = np.zeros((20, 20), dtype=np.float32)
    depth_map[10:, :] = 5.0  # bottom half is ground

    result = _detect_horizon_frac(depth_map)
    assert result is not None
    assert result == pytest.approx(0.5, abs=0.01)


def test_detect_horizon_frac_returns_none_all_sky():
    """All-zero depth map (all sky / no ground) → None."""
    from app.tools.estimate_height_above_surface import _detect_horizon_frac

    depth_map = np.zeros((20, 20), dtype=np.float32)
    result = _detect_horizon_frac(depth_map)
    assert result is None


def test_detect_horizon_frac_returns_none_all_ground():
    """All-5m depth map (no sky visible) → None, so caller uses horizontal fallback.

    Previously returned 0.0, which was misinterpreted as 'camera pointing 30° upward'
    and caused height = 0 for all frames with no sky (e.g. kitesurfing POV at water level).
    """
    from app.tools.estimate_height_above_surface import _detect_horizon_frac

    depth_map = np.full((20, 20), 5.0, dtype=np.float32)
    result = _detect_horizon_frac(depth_map)
    assert result is None


def test_tilt_corrected_height_horizontal_camera():
    """horizon_frac=0.5, vfov=60 → tilt=0°, bottom_strip=30° → height = depth × sin(30°) = 0.5 × depth."""
    from app.tools.estimate_height_above_surface import _tilt_corrected_height

    raw_depth = 10.0
    result = _tilt_corrected_height(raw_depth, horizon_frac=0.5, vfov_deg=60.0)
    assert result == pytest.approx(raw_depth * math.sin(math.radians(30.0)), abs=0.001)


def test_tilt_corrected_height_no_horizon_fallback():
    """horizon_frac=None → fallback to 0.5 (horizontal camera) → same as horizontal case."""
    from app.tools.estimate_height_above_surface import _tilt_corrected_height

    raw_depth = 10.0
    result_none = _tilt_corrected_height(raw_depth, horizon_frac=None, vfov_deg=60.0)
    result_half = _tilt_corrected_height(raw_depth, horizon_frac=0.5, vfov_deg=60.0)
    assert result_none == pytest.approx(result_half, abs=0.001)


def test_tilt_corrected_height_upward_pointing():
    """When bottom_strip_angle ≤ 0 (camera pointing upward), returns 0.0."""
    from app.tools.estimate_height_above_surface import _tilt_corrected_height

    # horizon_frac=0.0, vfov=60 → tilt=-30°, bottom_strip_angle=0° → 0.0
    result = _tilt_corrected_height(5.0, horizon_frac=0.0, vfov_deg=60.0)
    assert result == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_compute_heights_applies_tilt_correction():
    """_compute_heights uses tilt-corrected height, not raw depth."""
    from app.tools.estimate_height_above_surface import _compute_heights

    frames = _make_frames(1)
    # Depth map: top half sky (0.0), bottom half ground (6.0).
    # horizon_frac = 0.5 → tilt = 0°, bottom_strip = 30° → height = 6.0 × sin(30°) = 3.0
    depth_map = np.zeros((20, 20), dtype=np.float32)
    depth_map[10:, :] = 6.0

    with (
        patch(
            "app.tools.estimate_height_above_surface._download_frame",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch("cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        patch(
            "app.tools.estimate_height_above_surface._run_depth_inference",
            return_value=depth_map,
        ),
    ):
        records = await _compute_heights(
            frames, frame_batch_size=10, surface_sample_pct=0.5, camera_vfov_deg=60.0
        )

    assert len(records) == 1
    # raw_depth = 6.0, tilt-corrected = 6.0 × sin(30°) = 3.0 — not equal to raw 6.0
    assert records[0]["height_m"] != pytest.approx(6.0, abs=0.1)
    assert records[0]["height_m"] == pytest.approx(3.0, abs=0.01)


@pytest.mark.asyncio
async def test_compute_heights_stores_horizon_frac():
    """Each frame record includes a horizon_frac field."""
    from app.tools.estimate_height_above_surface import _compute_heights

    frames = _make_frames(1)
    depth_map = np.zeros((20, 20), dtype=np.float32)
    depth_map[10:, :] = 3.0  # horizon at row 10 → horizon_frac = 0.5

    with (
        patch(
            "app.tools.estimate_height_above_surface._download_frame",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch("cv2.imdecode", return_value=np.zeros((20, 20, 3), dtype=np.uint8)),
        patch(
            "app.tools.estimate_height_above_surface._run_depth_inference",
            return_value=depth_map,
        ),
    ):
        records = await _compute_heights(
            frames, frame_batch_size=10, surface_sample_pct=0.5, camera_vfov_deg=60.0
        )

    assert len(records) == 1
    assert "horizon_frac" in records[0]
    assert records[0]["horizon_frac"] == pytest.approx(0.5, abs=0.01)


# ── relative threshold tests ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uniform_depth_produces_no_events():
    """If all frames have the same depth, baseline == all heights and effective
    threshold > all heights → 0 events (fixes 'always one segment' on real POV video)."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    # All frames at 5.0 m — baseline = 5.0, effective = 5.5, nothing is airborne
    frame_records = [
        {"timestamp_seconds": float(i), "url": f"u{i}", "height_m": 5.0, "segment_index": -1}
        for i in range(5)
    ]

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.tools.estimate_height_above_surface.write_generated_asset",
            new=AsyncMock(return_value="http://blob/result.json"),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=frame_records),
        ),
    ):
        result = await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
            "height_threshold_m": 0.5,
        })

    assert result["summary"]["events_count"] == 0


@pytest.mark.asyncio
async def test_relative_threshold_detects_jumps_above_baseline():
    """Frames above (baseline + height_threshold_m) are airborne; frames at
    baseline height are not — even if all absolute values exceed 0.5 m."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    # Simulate forward-facing POV camera: baseline ≈ 3.0 m to water, jumps at 5–6 m.
    # 25th percentile of [3.0,3.0,3.0,5.0,6.0] ≈ 3.0 → effective threshold = 3.5 m.
    frame_records = [
        {"timestamp_seconds": 0.0, "url": "u0", "height_m": 3.0, "segment_index": -1},
        {"timestamp_seconds": 1.0, "url": "u1", "height_m": 3.0, "segment_index": -1},
        {"timestamp_seconds": 2.0, "url": "u2", "height_m": 5.0, "segment_index": -1},
        {"timestamp_seconds": 3.0, "url": "u3", "height_m": 6.0, "segment_index": -1},
        {"timestamp_seconds": 4.0, "url": "u4", "height_m": 3.0, "segment_index": -1},
    ]

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.tools.estimate_height_above_surface.write_generated_asset",
            new=AsyncMock(return_value="http://blob/result.json"),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=frame_records),
        ),
    ):
        result = await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
            "height_threshold_m": 0.5,
        })

    # Frames 2 and 3 are above baseline + 0.5 m — one event
    assert result["summary"]["events_count"] == 1
    assert result["summary"]["peak_height_m"] == pytest.approx(6.0, abs=0.01)


# ── estimate_height_above_surface (full function) tests ──────────────────────

@pytest.mark.asyncio
async def test_airborne_event_detection():
    """Consecutive frames above height_threshold_m are grouped into one event."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    # Frames 1 and 2 are above threshold (0.5 m); frames 0 and 3 are not
    frame_records = [
        {"timestamp_seconds": 0.0, "url": "u0", "height_m": 0.2, "segment_index": -1},
        {"timestamp_seconds": 1.0, "url": "u1", "height_m": 1.2, "segment_index": -1},
        {"timestamp_seconds": 2.0, "url": "u2", "height_m": 2.5, "segment_index": -1},
        {"timestamp_seconds": 3.0, "url": "u3", "height_m": 0.1, "segment_index": -1},
    ]

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=_make_frames(4)),
        ),
        patch(
            "app.tools.estimate_height_above_surface.write_generated_asset",
            new=AsyncMock(return_value="http://blob/result.json"),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=frame_records),
        ),
    ):
        result = await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
            "height_threshold_m": 0.5,
        })

    assert result["summary"]["events_count"] == 1
    assert result["summary"]["peak_height_m"] == pytest.approx(2.5, abs=0.01)
    assert result["result_asset"] == "http://blob/result.json"


@pytest.mark.asyncio
async def test_peak_height_m_in_summary():
    """Summary peak_height_m equals the highest event peak."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    frame_records = [
        {"timestamp_seconds": 0.0, "url": "u0", "height_m": 3.0, "segment_index": -1},
        {"timestamp_seconds": 1.0, "url": "u1", "height_m": 5.0, "segment_index": -1},
        {"timestamp_seconds": 2.0, "url": "u2", "height_m": 0.1, "segment_index": -1},
        {"timestamp_seconds": 3.0, "url": "u3", "height_m": 4.0, "segment_index": -1},
        {"timestamp_seconds": 4.0, "url": "u4", "height_m": 0.1, "segment_index": -1},
    ]

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.tools.estimate_height_above_surface.write_generated_asset",
            new=AsyncMock(return_value="http://blob/result.json"),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=frame_records),
        ),
    ):
        result = await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
            "height_threshold_m": 0.5,
        })

    assert result["summary"]["events_count"] == 2
    assert result["summary"]["peak_height_m"] == pytest.approx(5.0, abs=0.01)


@pytest.mark.asyncio
async def test_segment_index_assigned():
    """Frames inside events get 0-based segment_index; frames outside get -1."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    # Frame 3 (ts=3.0) ends the event → end_seconds=3.0; the <= check includes it in the segment.
    # Frame 4 (ts=4.0) is clearly after end_seconds and gets segment_index=-1.
    frame_records = [
        {"timestamp_seconds": 0.0, "url": "u0", "height_m": 0.1, "segment_index": -1},
        {"timestamp_seconds": 1.0, "url": "u1", "height_m": 1.5, "segment_index": -1},
        {"timestamp_seconds": 2.0, "url": "u2", "height_m": 2.0, "segment_index": -1},
        {"timestamp_seconds": 3.0, "url": "u3", "height_m": 0.1, "segment_index": -1},
        {"timestamp_seconds": 4.0, "url": "u4", "height_m": 0.1, "segment_index": -1},
    ]

    written_data = {}

    async def _capture_write(session_id, job_id, data_type, filename, data):
        written_data.update(data)
        return "http://blob/result.json"

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.tools.estimate_height_above_surface.write_generated_asset",
            new=AsyncMock(side_effect=_capture_write),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=frame_records),
        ),
    ):
        await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
            "height_threshold_m": 0.5,
        })

    frames = written_data["frames"]
    assert frames[0]["segment_index"] == -1   # before event
    assert frames[1]["segment_index"] == 0    # first airborne frame in segment
    assert frames[2]["segment_index"] == 1    # second airborne frame
    # frame 3 ends the event; end_seconds is set to its timestamp so it falls inside the segment range
    assert frames[3]["segment_index"] == 2
    assert frames[4]["segment_index"] == -1   # after end_seconds, outside all segments


@pytest.mark.asyncio
async def test_empty_frames_returns_gracefully():
    """No processable frames → zero events, no crash."""
    from app.tools.estimate_height_above_surface import estimate_height_above_surface

    with (
        patch(
            "app.tools.estimate_height_above_surface.read_generated_asset",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.tools.estimate_height_above_surface._compute_heights",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await estimate_height_above_surface({
            "frames_asset": "http://blob/frames.json",
            "job_id": "job-1",
        })

    assert result["summary"]["events_count"] == 0
    assert result["summary"]["peak_height_m"] == pytest.approx(0.0)
    assert result["result_asset"] is None


# ── registry tests ────────────────────────────────────────────────────────────

def test_tool_registered():
    """estimate_height_above_surface is in TOOLS with correct schema."""
    from app.tool_registry import TOOLS

    assert "estimate_height_above_surface" in TOOLS
    tool = TOOLS["estimate_height_above_surface"]

    assert tool["cost_tier"] == "free"
    assert tool["specialization"] == "sports"
    assert "height" in tool["capability_tags"]
    assert "pov" in tool["capability_tags"]

    props = tool["input_schema"]["properties"]
    assert "frames_asset" in props
    assert "job_id" in props
    assert "height_threshold_m" in props
    assert "camera_vfov_deg" in props

    # camera_vfov_deg must not be required (has a sensible default)
    required = tool["input_schema"]["required"]
    assert "frames_asset" in required
    assert "job_id" in required
    assert "height_threshold_m" not in required  # optional
    assert "camera_vfov_deg" not in required      # optional

    # Removed calibration inputs must not be present
    assert "calibration_frames" not in props
    assert "reference_height_m" not in props
    assert "airborne_threshold_pct" not in props


def test_catalogue_includes_tool():
    """get_tool_catalogue() returns an entry for the tool."""
    from app.tool_registry import get_tool_catalogue

    names = [t["name"] for t in get_tool_catalogue()]
    assert "estimate_height_above_surface" in names
