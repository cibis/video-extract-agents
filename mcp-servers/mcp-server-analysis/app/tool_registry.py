"""Tool registry — maps tool names to async functions and metadata."""
from app.tools.extract_frames import extract_frames
from app.tools.detect_motion import detect_motion
from app.tools.detect_objects import detect_objects
from app.tools.transcribe_audio import transcribe_audio
from app.tools.detect_motion_sports import detect_motion_sports
from app.tools.read_asset import read_asset
from app.tools.analyze_scene import analyze_scene
from app.tools.detect_objects_vision import detect_objects_vision
from app.tools.write_segments_asset import write_segments_asset
from app.tools.query_asset import query_asset, write_query_asset
from app.tools.estimate_height_above_surface import estimate_height_above_surface
from app.tools.ingest_video import ingest_video

TOOLS: dict[str, dict] = {
    "ingest_video": {
        "fn": ingest_video,
        "description": (
            "Download a video from a URL, upload it to Blob Storage, extract keyframes, "
            "and return the video_url and keyframe_index_asset needed to start the extraction pipeline. "
            "Use this as Phase 0 when a video file is attached via chat (LibreChat or Claude Desktop). "
            "MUST be called before extract_frames — it creates the keyframe_index_asset that "
            "extract_frames requires."
        ),
        "capability_tags": ["ingest", "upload", "keyframe_extraction"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg keyframe extraction — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_url": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "HTTP(S) or Azurite blob URL of the video, accessible from inside the container. "
                        "LibreChat file URLs (http://librechat-official:3080/api/files/...) work directly. "
                        "Azure Storage MCP blob URLs with localhost:10000 are automatically remapped to azurite:10000."
                    ),
                },
                "job_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Caller-generated UUID. Use the same job_id for all tool calls in this session.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session UUID. Groups this video with a session for follow-up jobs.",
                },
                "filename": {
                    "type": "string",
                    "description": "Optional desired filename in Blob Storage. Derived from source_url if omitted.",
                },
            },
            "required": ["source_url", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "video_url": {
                    "type": "string",
                    "description": "Blob URL of the uploaded original video — pass as video_url to extract_frames.",
                },
                "keyframe_index_asset": {
                    "type": "string",
                    "description": "Blob URL of the keyframe index JSON — pass as keyframe_index_asset to extract_frames.",
                },
                "session_id": {"type": "string"},
                "video_id": {"type": "string"},
                "frame_count": {"type": "integer"},
                "duration_seconds": {"type": "number"},
            },
        },
    },
    "extract_frames": {
        "fn": extract_frames,
        "description": (
            "Return keyframe images from the pre-computed keyframe index. "
            "MUST be called before any detect_objects* or motion tool — it returns a frames_asset "
            "blob URL that all detection tools require as input."
        ),
        "capability_tags": ["frames", "keyframes"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "No model calls; reads pre-indexed keyframes from DB/blob.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "keyframe_index_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL of the keyframe index asset (provided in job context).",
                },
                "frame_indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                },
            },
            "required": ["video_url", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": "Blob URL — pass as frames_asset to detect_* tools.",
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "frames_returned": {"type": "integer"},
                        "fps": {"type": "number"},
                        "start_seconds": {"type": "number"},
                        "end_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "detect_motion": {
        "fn": detect_motion,
        "description": (
            "Compute a motion score and identify high-motion segments using optical flow. "
            "Requires frames_asset from extract_frames. "
            "Use frame_batch_size matching the total frame count for short clips, "
            "or 50–100 for longer videos."
        ),
        "capability_tags": ["motion", "optical_flow"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local OpenCV optical flow — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL returned by extract_frames (result_asset field).",
                },
                "frame_batch_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": (
                        "Frames to process per batch. 50–100 is safe for memory; "
                        "use total frame count for short clips."
                    ),
                    "default": 50,
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["frames_asset", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of the full motion result. The blob contains: "
                        "'segments' (each with start_seconds, end_seconds, first_frame_index, last_frame_index) and "
                        "'frames' (each with timestamp_seconds, motion_score, url, segment_index — "
                        "segment_index is the 0-based position of the frame within its segment, or -1 if outside all segments). "
                        "Pass result_asset to write_query_asset to filter frames for frontier tools."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "segments": {"type": "array"},
                        "motion_score": {"type": "number"},
                        "high_motion_segments_count": {"type": "integer"},
                        "total_motion_duration_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "detect_motion_sports": {
        "fn": detect_motion_sports,
        "description": (
            "Detect high-intensity sports motion events (jumps, tricks, fast actions) "
            "using a sports-tuned optical flow model. "
            "Requires frames_asset from extract_frames. "
            "Prefer over detect_motion for sports/action content."
        ),
        "capability_tags": ["motion", "sports", "events"],
        "specialization": "sports",
        "cost_tier": "free",
        "cost_note": "Local OpenCV optical flow — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL returned by extract_frames (result_asset field).",
                },
                "frame_batch_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": (
                        "Frames to process per batch. 50–100 is safe for memory; "
                        "use total frame count for short clips."
                    ),
                    "default": 50,
                },
                "sensitivity": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Motion sensitivity threshold 0.0–1.0. Default 0.5.",
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["frames_asset", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of the full motion result. The blob contains: "
                        "'events' (each with start_seconds, end_seconds, type, first_frame_index, last_frame_index), "
                        "'segments' (same time ranges as events, also with first_frame_index, last_frame_index), and "
                        "'frames' (each with timestamp_seconds, motion_score, url, segment_index — "
                        "segment_index is the 0-based position of the frame within its event/segment, or -1 if outside all segments). "
                        "Pass result_asset to write_query_asset to filter frames for frontier tools."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "segments": {"type": "array"},
                        "events_count": {"type": "integer"},
                        "peak_motion_score": {"type": "number"},
                        "total_event_duration_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "detect_objects": {
        "fn": detect_objects,
        "description": (
            "Detect objects in video frames using YOLO-World open-vocabulary detection. "
            "Accepts any text description as object class — not limited to COCO classes. "
            "Examples: 'water', 'ocean', 'kite', 'person', 'wave', 'surfboard', 'dog'. "
            "Requires frames_asset from extract_frames. Returns segments where the specified objects appear. "
            "Prefer over detect_objects_vision for speed; use detect_objects_vision for complex "
            "reasoning or when spatial/contextual description is needed."
        ),
        "capability_tags": ["detection", "objects"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local YOLO-World inference — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL returned by extract_frames (result_asset field).",
                },
                "frame_batch_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Frames to process per batch. Default 50.",
                    "default": 50,
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "object_classes": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                    "description": (
                        "Object descriptions to detect. Any text label works — not limited to COCO classes. "
                        "Examples: ['water', 'kite', 'person'], ['ocean wave', 'surfer'], ['car', 'traffic light']. "
                        "Use specific descriptions for better accuracy."
                    ),
                },
            },
            "required": ["frames_asset", "job_id", "object_classes"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of the full detections result. The blob contains: "
                        "'segments' (each with start_seconds, end_seconds, classes, first_frame_index, last_frame_index) and "
                        "'frames' (each with timestamp_seconds, url, detection_count, detected_classes, segment_index — "
                        "segment_index is the 0-based position of the frame within its segment, or -1 if outside all segments). "
                        "Pass result_asset to write_query_asset to filter frames for frontier tools."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "segments": {"type": "array"},
                        "classes_detected": {"type": "array"},
                        "total_detections": {"type": "integer"},
                        "total_duration_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "query_asset": {
        "fn": query_asset,
        "description": (
            "Apply a JSONPath expression to any blob asset and return only the matching values. "
            "Use instead of read_asset when you need a targeted subset of a large result blob "
            "(e.g. timestamps of high-motion frames, specific detection segments). "
            "Avoids loading full blob content into the agent context window."
        ),
        "capability_tags": ["assets", "query", "jsonpath"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob download + local JSONPath filter — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "blob_url": {
                    "type": "string",
                    "minLength": 1,
                    "description": "URL of any generated asset blob.",
                },
                "jsonpath": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "JSONPath expression to apply, e.g. "
                        "'$.frames[*].timestamp_seconds' or "
                        "'$.high_motion_segments[*]'."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum matched values to return. Default 50.",
                    "default": 50,
                },
            },
            "required": ["blob_url", "jsonpath"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "matches": {"type": "array"},
                "total_matches": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
    },
    "write_query_asset": {
        "fn": write_query_asset,
        "description": (
            "Filter a detection result_asset blob with a JSONPath expression and write the "
            "matching frames to a new blob. "
            "Use this as the bridge between free detection tools and frontier vision tools: "
            "after detect_motion_sports or detect_objects returns a result_asset, call "
            "write_query_asset with '$.frames[*]' (or a filtered sub-expression) on that "
            "result_asset to produce a frames_asset blob; then pass that frames_asset directly "
            "to analyze_scene or detect_objects_vision. "
            "This keeps frontier API cost proportional to the number of candidate frames, not "
            "the full video length. "
            "Always pass video_url and job_id so the output blob is correctly scoped."
        ),
        "capability_tags": ["assets", "query", "jsonpath", "frames_asset"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob download + local JSONPath filter — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "blob_url": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "result_asset URL from a detection tool "
                        "(e.g. detect_motion_sports, detect_objects)."
                    ),
                },
                "jsonpath": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "JSONPath expression selecting the frames to keep from the detection result blob. "
                        "Examples: '$.frames[*]' for all frames, "
                        "'$.frames[?(@.segment_index == 0)]' for the first frame of every segment, "
                        "'$.frames[?(@.segment_index >= 0)]' for all in-segment frames, "
                        "'$.frames[?(@.motion_score > 0.7)]' for high-motion frames, "
                        "'$.frames[?(@.detection_count > 0)]' for frames with detections."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum frames to include. Default 50.",
                    "default": 50,
                },
            },
            "required": ["blob_url", "jsonpath", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of the filtered frames — pass as frames_asset to "
                        "analyze_scene or detect_objects_vision."
                    ),
                },
                "matches": {"type": "array"},
                "total_matches": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
    },

    "read_asset": {
        "fn": read_asset,
        "description": (
            "Read a non-video session asset (JSON, text, CSV, image) from Blob Storage "
            "and return its content as a string. "
            "Use query_asset instead for large JSON blobs to avoid context window bloat."
        ),
        "capability_tags": ["assets", "read", "text", "json"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob download only — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "blob_url": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL of the asset to read.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum bytes to read. Defaults to 1MB.",
                },
            },
            "required": ["blob_url"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "content_type": {"type": "string"},
                "size_bytes": {"type": "integer"},
            },
        },
    },
    "analyze_scene": {
        "fn": analyze_scene,
        "description": (
            "Use a Claude vision model to semantically describe scenes across multiple video frames. "
            "Returns structured per-frame descriptions (activities, objects, setting, mood) "
            "as a result_asset blob + compact summary. "
            "FRONTIER TOOL — incurs API cost per batch. "
            "Use only when YOLO-style detection is insufficient. "
            "Sample frames sparingly before running on all frames."
        ),
        "capability_tags": ["vision", "frontier", "scene", "semantic"],
        "specialization": "frontier_vision",
        "cost_tier": "frontier",
        "cost_note": (
            "API calls per batch are determined automatically based on model context window, "
            "task type, and image resolution — no frame_batch_size parameter is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Blob URL of the frames to analyse. "
                        "Pass result_asset from extract_frames for full-video analysis, "
                        "or result_asset from write_query_asset to analyse only a "
                        "pre-filtered subset (e.g. high-motion frames from detect_motion_sports)."
                    ),
                },
                "question": {
                    "type": "string",
                    "description": "Optional specific question to answer per frame.",
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["frames_asset", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of per-frame scene analysis. "
                        "The blob contains: frames_analysed (int), model_used (str), "
                        "frames_with_errors (int), total_batches (int), "
                        "batches (array — per-batch metadata: batch_index, frames_in_batch, "
                        "resolution [w,h], frame_range [start,end]), "
                        "errors (array — per-frame errors: frame_index, timestamp_seconds, "
                        "error_type ('no_response'|'no_url'), error_detail, traceback), "
                        "frames (array — per-frame results: index, timestamp_seconds, "
                        "description, objects, activities, setting, mood). "
                        "Use query_asset with a JSONPath such as '$.frames[*]' or "
                        "'$.frames[?(@.setting == \"outdoor\")]' to read specific values."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "frames_analysed": {"type": "integer"},
                        "unique_settings": {"type": "array"},
                        "common_objects": {"type": "array"},
                        "model_used": {"type": "string"},
                    },
                },
            },
        },
    },
    "detect_objects_vision": {
        "fn": detect_objects_vision,
        "description": (
            "Use a Claude vision model for open-vocabulary object detection across video frames. "
            "Unlike YOLO, accepts any natural language description "
            "(e.g. 'kitesurfer mid-jump', 'person wearing red helmet'). "
            "Returns per-frame detections as a result_asset blob + compact summary. "
            "FRONTIER TOOL — incurs API cost per batch. "
            "Use only when YOLO cannot identify the target. "
            "First run detect_objects for standard COCO classes."
        ),
        "capability_tags": ["detection", "vision", "frontier", "open_vocabulary"],
        "specialization": "frontier_vision",
        "cost_tier": "frontier",
        "cost_note": (
            "API calls per batch are determined automatically based on model context window, "
            "task type, and image resolution — no frame_batch_size parameter is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "Blob URL of the frames to analyse. "
                        "Pass result_asset from extract_frames for full-video analysis, "
                        "or result_asset from write_query_asset to analyse only a "
                        "pre-filtered subset (e.g. high-motion frames from detect_motion_sports)."
                    ),
                },
                "object_descriptions": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                    "description": "Natural language descriptions of objects to detect.",
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["frames_asset", "object_descriptions", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of per-frame object detection results. "
                        "The blob contains: frames_analysed (int), object_descriptions (list[str]), "
                        "model_used (str), frames_with_errors (int), total_batches (int), "
                        "batches (array — per-batch metadata: batch_index, frames_in_batch, "
                        "resolution [w,h], frame_range [start,end]), "
                        "errors (array — per-frame errors: frame_index, timestamp_seconds, "
                        "error_type ('no_response'|'no_url'), error_detail, traceback), "
                        "frames (array — per-frame results: index, timestamp_seconds, "
                        "detections [{object, present, confidence, location_description, bbox_rough}]). "
                        "Use query_asset with '$.frames[?(@.detections[0].present == true)]' "
                        "to find frames with detections, or '$.frames[*].index' to retrieve "
                        "the original frame indices."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "frames_analysed": {"type": "integer"},
                        "objects_searched": {"type": "array"},
                        "frames_with_detections": {"type": "integer"},
                        "model_used": {"type": "string"},
                    },
                },
            },
        },
    },
    "transcribe_audio": {
        "fn": transcribe_audio,
        "description": "Transcribe audio from a video file using Whisper.",
        "capability_tags": ["audio", "transcription", "speech"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local Whisper inference — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "language": {"type": "string", "default": "en"},
            },
            "required": ["video_url", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {"type": "string"},
                "summary": {
                    "type": "object",
                    "properties": {
                        "word_count": {"type": "integer"},
                        "segment_count": {"type": "integer"},
                        "duration_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "estimate_height_above_surface": {
        "fn": estimate_height_above_surface,
        "description": (
            "Estimate camera height above the ground or water surface in first-person "
            "(POV/GoPro-style) footage. Uses Depth Anything V2 Metric Outdoor to produce "
            "absolute depth in metres per frame — no calibration or reference input required. "
            "Returns airborne events with peak height in metres and a result asset blob URL. "
            "NOT suitable for third-person footage — use detect_motion_sports or detect_objects "
            "for videos where the subject is visible in frame."
        ),
        "capability_tags": ["height", "jump", "measurement", "sports", "depth", "pov", "first-person"],
        "specialization": "sports",
        "cost_tier": "free",
        "cost_note": "Local Depth Anything V2 Metric inference — no API calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "frames_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL returned by extract_frames (result_asset field).",
                },
                "frame_batch_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": (
                        "Frames to process per batch. Default 20 (smaller than motion tools "
                        "due to Depth Anything V2 inference cost per frame)."
                    ),
                    "default": 20,
                },
                "surface_sample_pct": {
                    "type": "number",
                    "minimum": 0.05,
                    "maximum": 0.5,
                    "description": (
                        "Fraction of frame height (from the bottom) used to sample surface "
                        "depth. Default 0.20 (bottom 20% of the frame). Increase if the "
                        "surface occupies more of the frame."
                    ),
                    "default": 0.20,
                },
                "height_threshold_m": {
                    "type": "number",
                    "minimum": 0.05,
                    "description": (
                        "Camera height in metres above the surface to count as airborne. "
                        "Default 2. Lower = more sensitive to small hops."
                    ),
                    "default": 2,
                },
                "camera_vfov_deg": {
                    "type": "number",
                    "minimum": 10,
                    "maximum": 150,
                    "description": (
                        "Vertical field of view of the camera in degrees. Default 60 (GoPro wide-angle). "
                        "Used for tilt correction: higher values = wider lens. "
                        "Common values: GoPro Hero wide ~94, GoPro linear ~49, standard webcam ~60."
                    ),
                    "default": 60,
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["frames_asset", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of the full height analysis result. The blob contains: "
                        "'peak_height_m' (float), "
                        "'events' (each with start_seconds, end_seconds, type, peak_height_m, first_frame_index, last_frame_index), "
                        "'segments' (same time ranges as events, also with first_frame_index, last_frame_index), and "
                        "'frames' (each with timestamp_seconds, height_m, horizon_frac, url, segment_index — "
                        "height_m is the tilt-corrected camera height in metres; "
                        "horizon_frac is the detected horizon row as a fraction of frame height (0=top, 1=bottom), or null if no horizon found; "
                        "segment_index is the 0-based position of the frame within its event/segment, or -1 if outside all segments). "
                        "Pass result_asset to write_query_asset to filter frames for further analysis."
                    ),
                },
                "summary": {
                    "type": "object",
                    "properties": {
                        "segments": {"type": "array"},
                        "events_count": {"type": "integer"},
                        "peak_height_m": {"type": "number"},
                        "total_event_duration_seconds": {"type": "number"},
                    },
                },
            },
        },
    },
    "write_segments_asset": {
        "fn": write_segments_asset,
        "description": (
            "Save the final merged segments list to blob storage. "
            "Call this ONCE after all detection/motion tools have run. "
            "Returns segments_asset URL — pass directly to extract_clips_bulk.\n"
            "IMPORTANT: every segment MUST include 'video_url' — the source video URL "
            "that the segment was detected from. Detection tools include 'video_url' in "
            "each segment they return; do NOT strip it when merging. "
            "extract_clips_bulk requires 'video_url' per segment to extract clips from "
            "the correct source video — missing 'video_url' will cause extraction to fail.\n"
            "Example: {\"segments\": [{\"start_seconds\": 4.5, \"end_seconds\": 6.0, "
            "\"video_url\": \"http://...\"}], "
            "\"job_id\": \"<uuid>\", \"session_id\": \"<uuid>\"}"
        ),
        "capability_tags": ["segments", "write", "assets"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob write only — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array",
                    "minItems": 1,
                    "description": (
                        "List of segment dicts. Each must include start_seconds, end_seconds, "
                        "and video_url (the source video the segment was detected from). "
                        "end_seconds must be strictly greater than start_seconds. "
                        "Example: [{\"start_seconds\": 4.5, \"end_seconds\": 6.0, "
                        "\"video_url\": \"http://...\"}]"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "description": "Clip start time in seconds (inclusive). Must be >= 0.",
                            },
                            "end_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "description": "Clip end time in seconds (exclusive). Must be strictly greater than start_seconds.",
                            },
                            "video_url": {
                                "type": "string",
                                "description": "Source video URL this segment was detected from. Required — used by extract_clips_bulk to extract from the correct video.",
                            },
                        },
                        "required": ["start_seconds", "end_seconds", "video_url"],
                    },
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["segments", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "segments_asset": {
                    "type": "string",
                    "description": "Blob URL of the merged segments JSON — pass as segments_asset to extract_clips_bulk.",
                },
                "segments_count": {"type": "integer"},
            },
        },
    },
}


def get_tool_catalogue() -> list[dict]:
    return [
        {
            "name": name,
            "description": meta["description"],
            "capability_tags": meta.get("capability_tags", []),
            "specialization": meta.get("specialization", "general"),
            "cost_tier": meta.get("cost_tier", "free"),
            "cost_note": meta.get("cost_note", ""),
            "input_schema": meta["input_schema"],
            "output_schema": meta["output_schema"],
        }
        for name, meta in TOOLS.items()
    ]
