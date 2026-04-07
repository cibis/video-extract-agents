"""Unit tests for app/validation.py."""
import pytest
from app.validation import validate_tool_payload

# ---------------------------------------------------------------------------
# Minimal valid schemas used as fixtures
# ---------------------------------------------------------------------------

_EXTRACT_FRAMES_SCHEMA = {
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "minLength": 1},
        "job_id": {"type": "string", "minLength": 1},
        "keyframe_index_asset": {"type": "string", "minLength": 1},
        "frame_indices": {"type": "array", "items": {"type": "integer", "minimum": 0}},
    },
    "required": ["video_url", "job_id", "keyframe_index_asset"],
}

_DETECT_OBJECTS_SCHEMA = {
    "type": "object",
    "properties": {
        "frames_asset": {"type": "string", "minLength": 1},
        "job_id": {"type": "string", "minLength": 1},
        "frame_batch_size": {"type": "integer", "minimum": 1, "maximum": 500},
        "object_classes": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
    },
    "required": ["frames_asset", "job_id", "object_classes"],
}

_WRITE_SEGMENTS_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number", "minimum": 0},
                    "end_seconds": {"type": "number", "minimum": 0},
                },
                "required": ["start_seconds", "end_seconds"],
            },
        },
        "job_id": {"type": "string", "minLength": 1},
    },
    "required": ["segments", "job_id"],
}

_DETECT_MOTION_SPORTS_SCHEMA = {
    "type": "object",
    "properties": {
        "frames_asset": {"type": "string", "minLength": 1},
        "job_id": {"type": "string", "minLength": 1},
        "sensitivity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["frames_asset", "job_id"],
}


# ---------------------------------------------------------------------------
# Valid payload — no errors
# ---------------------------------------------------------------------------

def test_valid_extract_frames():
    payload = {
        "video_url": "https://blob/video.mp4",
        "job_id": "job-123",
        "keyframe_index_asset": "https://blob/kf.json",
    }
    assert validate_tool_payload("extract_frames", _EXTRACT_FRAMES_SCHEMA, payload) == []


def test_valid_detect_objects():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": ["person", "dog"],
    }
    assert validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload) == []


def test_valid_write_segments():
    payload = {
        "segments": [{"start_seconds": 0.0, "end_seconds": 5.0}],
        "job_id": "job-123",
    }
    assert validate_tool_payload("write_segments_asset", _WRITE_SEGMENTS_SCHEMA, payload) == []


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------

def test_missing_required_field():
    payload = {"video_url": "https://blob/video.mp4", "keyframe_index_asset": "https://blob/kf.json"}
    errors = validate_tool_payload("extract_frames", _EXTRACT_FRAMES_SCHEMA, payload)
    assert any("job_id" in e for e in errors), f"Expected job_id error in {errors}"


def test_missing_multiple_required_fields():
    payload = {}
    errors = validate_tool_payload("extract_frames", _EXTRACT_FRAMES_SCHEMA, payload)
    assert len(errors) >= 3  # video_url, job_id, keyframe_index_asset all missing


# ---------------------------------------------------------------------------
# Wrong type
# ---------------------------------------------------------------------------

def test_wrong_type_string_for_integer():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": ["person"],
        "frame_batch_size": "fifty",  # should be integer
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("frame_batch_size" in e for e in errors), f"Expected type error in {errors}"


def test_wrong_type_number_for_array():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": "person",  # should be array
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("object_classes" in e for e in errors)


# ---------------------------------------------------------------------------
# Empty string on minLength field
# ---------------------------------------------------------------------------

def test_empty_string_on_required_url():
    payload = {
        "video_url": "",  # empty — violates minLength: 1
        "job_id": "job-123",
        "keyframe_index_asset": "https://blob/kf.json",
    }
    errors = validate_tool_payload("extract_frames", _EXTRACT_FRAMES_SCHEMA, payload)
    assert any("video_url" in e for e in errors)


def test_empty_job_id():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "",  # empty
        "object_classes": ["person"],
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("job_id" in e for e in errors)


# ---------------------------------------------------------------------------
# Numeric out-of-range
# ---------------------------------------------------------------------------

def test_frame_batch_size_too_low():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": ["person"],
        "frame_batch_size": 0,  # below minimum: 1
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("frame_batch_size" in e for e in errors)


def test_frame_batch_size_too_high():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": ["person"],
        "frame_batch_size": 501,  # above maximum: 500
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("frame_batch_size" in e for e in errors)


def test_sensitivity_out_of_range():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "sensitivity": 1.5,  # above maximum: 1.0
    }
    errors = validate_tool_payload("detect_motion_sports", _DETECT_MOTION_SPORTS_SCHEMA, payload)
    assert any("sensitivity" in e for e in errors)


# ---------------------------------------------------------------------------
# Empty required array
# ---------------------------------------------------------------------------

def test_empty_object_classes():
    payload = {
        "frames_asset": "https://blob/frames.json",
        "job_id": "job-123",
        "object_classes": [],  # violates minItems: 1
    }
    errors = validate_tool_payload("detect_objects", _DETECT_OBJECTS_SCHEMA, payload)
    assert any("object_classes" in e for e in errors)


def test_empty_segments_list():
    payload = {"segments": [], "job_id": "job-123"}
    errors = validate_tool_payload("write_segments_asset", _WRITE_SEGMENTS_SCHEMA, payload)
    assert any("segments" in e for e in errors)


# ---------------------------------------------------------------------------
# Cross-field: end_seconds <= start_seconds
# ---------------------------------------------------------------------------

def test_segment_end_not_greater_than_start():
    payload = {
        "segments": [{"start_seconds": 5.0, "end_seconds": 5.0}],
        "job_id": "job-123",
    }
    errors = validate_tool_payload("write_segments_asset", _WRITE_SEGMENTS_SCHEMA, payload)
    assert any("end_seconds" in e and "start_seconds" in e for e in errors)


def test_segment_end_less_than_start():
    payload = {
        "segments": [{"start_seconds": 10.0, "end_seconds": 3.0}],
        "job_id": "job-123",
    }
    errors = validate_tool_payload("write_segments_asset", _WRITE_SEGMENTS_SCHEMA, payload)
    assert any("end_seconds" in e and "start_seconds" in e for e in errors)


def test_segment_valid_range():
    payload = {
        "segments": [{"start_seconds": 0.0, "end_seconds": 10.0}],
        "job_id": "job-123",
    }
    assert validate_tool_payload("write_segments_asset", _WRITE_SEGMENTS_SCHEMA, payload) == []


# ---------------------------------------------------------------------------
# Cross-field: merge_clips must have clip_list_asset or clip_urls
# ---------------------------------------------------------------------------

_MERGE_CLIPS_SCHEMA = {
    "type": "object",
    "properties": {
        "clip_list_asset": {"type": "string", "minLength": 1},
        "clip_urls": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "output_name": {"type": "string"},
    },
}


def test_merge_clips_missing_both_sources():
    errors = validate_tool_payload("merge_clips", _MERGE_CLIPS_SCHEMA, {})
    assert any("clip_list_asset" in e or "clip_urls" in e for e in errors)


def test_merge_clips_with_clip_list_asset():
    payload = {"clip_list_asset": "https://blob/clip_list.json"}
    assert validate_tool_payload("merge_clips", _MERGE_CLIPS_SCHEMA, payload) == []


def test_merge_clips_with_clip_urls():
    payload = {"clip_urls": ["https://blob/clip1.mp4"]}
    assert validate_tool_payload("merge_clips", _MERGE_CLIPS_SCHEMA, payload) == []


# ---------------------------------------------------------------------------
# Cross-field: extract_clips_bulk must have segments_asset or segments
# ---------------------------------------------------------------------------

_EXTRACT_CLIPS_BULK_SCHEMA = {
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "minLength": 1},
        "job_id": {"type": "string", "minLength": 1},
        "segments_asset": {"type": "string", "minLength": 1},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start_seconds": {"type": "number", "minimum": 0},
                    "end_seconds": {"type": "number", "minimum": 0},
                },
                "required": ["start_seconds", "end_seconds"],
            },
        },
    },
    "required": ["video_url", "job_id"],
}


def test_extract_clips_bulk_missing_segments_source():
    payload = {"video_url": "https://blob/video.mp4", "job_id": "job-123"}
    errors = validate_tool_payload("extract_clips_bulk", _EXTRACT_CLIPS_BULK_SCHEMA, payload)
    assert any("segments_asset" in e or "segments" in e for e in errors)


def test_extract_clips_bulk_with_segments_asset():
    payload = {
        "video_url": "https://blob/video.mp4",
        "job_id": "job-123",
        "segments_asset": "https://blob/segments.json",
    }
    assert validate_tool_payload("extract_clips_bulk", _EXTRACT_CLIPS_BULK_SCHEMA, payload) == []


def test_extract_clips_bulk_with_inline_segments():
    payload = {
        "video_url": "https://blob/video.mp4",
        "job_id": "job-123",
        "segments": [{"start_seconds": 0.0, "end_seconds": 5.0}],
    }
    assert validate_tool_payload("extract_clips_bulk", _EXTRACT_CLIPS_BULK_SCHEMA, payload) == []
