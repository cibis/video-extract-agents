"""Tool registry for mcp-server-processing."""
from app.tools.split_video import split_video
from app.tools.extract_clip import extract_clip
from app.tools.extract_clips_bulk import extract_clips_bulk
from app.tools.merge_clips import merge_clips
from app.tools.transform_video import transform_video
from app.tools.write_asset import write_asset
from app.tools.patch_asset import patch_asset
from app.tools.normalize_segments import normalize_segments
from app.tools.query_asset import query_asset, write_query_asset

TOOLS: dict[str, dict] = {
    "split_video": {
        "fn": split_video,
        "description": "Split a video into fixed-length segments.",
        "capability_tags": ["split", "segments", "video"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "segment_length_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 36000,
                    "default": 30,
                },
            },
            "required": ["video_url"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"segment_urls": {"type": "array"}},
        },
    },
    "extract_clip": {
        "fn": extract_clip,
        "description": (
            "Extract a time-bounded clip from a video. "
            "Appends the clip URL to a running clip_list blob and returns clip_list_asset. "
            "Pass clip_list_asset from each call to the next extract_clip call, then to merge_clips."
        ),
        "capability_tags": ["clip", "extract", "video"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "start_seconds": {"type": "number", "minimum": 0},
                "end_seconds": {"type": "number", "minimum": 0},
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "clip_list_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL of existing clip list to append to (from previous extract_clip).",
                },
                "video_duration_seconds": {"type": "number", "minimum": 0},
                "output_name": {"type": "string"},
            },
            "required": ["video_url", "start_seconds", "end_seconds", "job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "clip_url": {"type": "string"},
                "clip_list_asset": {
                    "type": "string",
                    "description": "Blob URL of updated clip list — pass to next extract_clip or merge_clips.",
                },
                "clips_collected": {"type": "integer"},
            },
        },
    },
    "extract_clips_bulk": {
        "fn": extract_clips_bulk,
        "description": (
            "Extract all identified segments in one call — "
            "use instead of chained extract_clip calls when segments_asset is available. "
            "Each segment must carry 'video_url' identifying its source video — "
            "segments from write_segments_asset already include this. "
            "For multi-video jobs each segment is extracted from its own source video. "
            "Returns clip_list_asset — pass directly to merge_clips."
        ),
        "capability_tags": ["clip", "extract", "bulk", "video"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
                "segments_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL of merged segments JSON from write_segments_asset (preferred). Each segment must include 'video_url'.",
                },
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start_seconds": {"type": "number", "minimum": 0},
                            "end_seconds": {"type": "number", "minimum": 0},
                            "video_url": {
                                "type": "string",
                                "description": "Source video URL for this segment (required).",
                            },
                        },
                        "required": ["start_seconds", "end_seconds", "video_url"],
                    },
                    "description": (
                        "Inline segments fallback — each entry must include start_seconds, "
                        "end_seconds, and video_url. end_seconds must be > start_seconds."
                    ),
                },
                "video_duration_seconds": {"type": "number", "minimum": 0},
                "output_prefix": {"type": "string"},
            },
            "required": ["job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "clip_list_asset": {
                    "type": "string",
                    "description": "Blob URL of clip_list.json — pass directly to merge_clips.",
                },
                "clips_extracted": {"type": "integer"},
                "clip_urls": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "merge_clips": {
        "fn": merge_clips,
        "description": (
            "Concatenate multiple video clips into a single output. "
            "Accepts clip_list_asset (blob URL from extract_clip or extract_clips_bulk) "
            "or clip_urls list as fallback. "
            "One of clip_list_asset or clip_urls must be provided."
        ),
        "capability_tags": ["merge", "concatenate", "video"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "clip_list_asset": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Blob URL of clip list written by extract_clip or extract_clips_bulk (preferred).",
                },
                "clip_urls": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Fallback list of clip URLs if clip_list_asset not provided.",
                },
                "output_name": {"type": "string"},
            },
        },
        "output_schema": {
            "type": "object",
            "properties": {"output_url": {"type": "string"}},
        },
    },
    "transform_video": {
        "fn": transform_video,
        "description": "Apply transformations (resize, speed, color grade) to a video.",
        "capability_tags": ["transform", "resize", "color", "video"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Local FFmpeg — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "video_url": {"type": "string", "minLength": 1},
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["resize", "speed", "color_grade"],
                            },
                            "params": {"type": "object"},
                        },
                        "required": ["type"],
                    },
                    "description": (
                        "List of operations to apply in order. "
                        "Each operation must have a 'type' of 'resize', 'speed', or 'color_grade' "
                        "and an optional 'params' object."
                    ),
                },
                "output_name": {"type": "string"},
            },
            "required": ["video_url"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"output_url": {"type": "string"}},
        },
    },
    "write_asset": {
        "fn": write_asset,
        "description": (
            "Write a generated non-video asset (JSON, text, CSV) to Blob Storage "
            "and return its blob URL. Use this to persist analysis results, "
            "structured data, or intermediate outputs for later tools or download."
        ),
        "capability_tags": ["assets", "write", "json", "text"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob write only — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Text content to write.",
                },
                "filename": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Output filename (e.g. 'analysis.json').",
                },
                "content_type": {
                    "type": "string",
                    "description": "MIME type, e.g. 'application/json' or 'text/plain'.",
                    "default": "application/json",
                },
                "session_id": {
                    "type": "string",
                    "description": "Session ID for scoping the blob path.",
                },
            },
            "required": ["content", "filename"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "blob_url": {"type": "string"},
                "filename": {"type": "string"},
                "size_bytes": {"type": "integer"},
            },
        },
    },
    "normalize_segments": {
        "fn": normalize_segments,
        "description": (
            "Expand short segments to a minimum duration (centered on their midpoint), "
            "merge overlapping segments from the same video, and enforce video boundaries. "
            "Accepts a segments_asset blob URL (from write_segments_asset) or an inline segments list. "
            "Writes the result as a new segments_asset blob — pass directly to extract_clips_bulk. "
            "Use after write_segments_asset to normalize all segments before clip extraction."
        ),
        "capability_tags": ["segments", "normalize", "expand", "merge"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob read + write + local arithmetic — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segments_asset": {
                    "type": "string",
                    "description": (
                        "Blob URL of a segments_asset written by write_segments_asset. "
                        "Provide this OR inline segments, not both."
                    ),
                },
                "segments": {
                    "type": "array",
                    "description": "Inline segments list. Provide this OR segments_asset, not both.",
                    "items": {"type": "object"},
                },
                "min_duration_seconds": {
                    "type": "number",
                    "default": 3.0,
                    "description": (
                        "Expand any segment shorter than this to exactly min_duration_seconds, "
                        "centered on the original midpoint. Default 3.0."
                    ),
                },
                "merge_overlapping": {
                    "type": "boolean",
                    "default": True,
                    "description": "Merge segments from the same video_url that overlap or are contiguous. Default true.",
                },
                "video_durations": {
                    "type": "object",
                    "description": (
                        "Optional map of {video_url: duration_seconds}. "
                        "When provided for a video, end_seconds is clamped to that duration "
                        "and start is slid back as far as possible without going negative. "
                        "Omit to enforce only the start≥0 boundary."
                    ),
                },
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},
            },
            "required": ["job_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "segments_asset": {
                    "type": "string",
                    "description": "Blob URL of normalized segments — pass directly to extract_clips_bulk.",
                },
                "segments_count": {"type": "integer", "description": "Total segments after normalization."},
                "expanded_count": {"type": "integer", "description": "Segments that were expanded to min_duration."},
                "merged_count":   {"type": "integer", "description": "Segments removed by overlap merging."},
            },
        },
    },
    "patch_asset": {
        "fn": patch_asset,
        "description": (
            "Apply RFC 6902 JSON Patch operations to an existing JSON blob in-place. "
            "Reads the target blob, applies the operations, and writes the result back to the "
            "same URL. Returns only a brief summary — does not return the modified content. "
            "Use to update fields in an existing analysis result or asset without rewriting the entire file."
        ),
        "capability_tags": ["assets", "patch", "json", "update"],
        "specialization": "general",
        "cost_tier": "free",
        "cost_note": "Blob read + write only — no model calls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "blob_url": {
                    "type": "string",
                    "minLength": 1,
                    "description": "URL of the JSON blob to patch (must contain a valid JSON document).",
                },
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "description": (
                        "RFC 6902 JSON Patch operations. Each item must have 'op' and 'path'; "
                        "'value' is required for add/replace/test; 'from' is required for move/copy. "
                        "Supported ops: add, remove, replace, move, copy, test. "
                        "Example: [{\"op\": \"replace\", \"path\": \"/status\", \"value\": \"done\"}]"
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["add", "remove", "replace", "move", "copy", "test"]},
                            "path": {"type": "string"},
                            "value": {},
                            "from": {"type": "string"},
                        },
                        "required": ["op", "path"],
                    },
                },
                "job_id": {"type": "string", "description": "Job ID for logging context."},
                "session_id": {"type": "string", "description": "Session ID for logging context."},
            },
            "required": ["blob_url", "operations"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "blob_url": {"type": "string", "description": "Same URL as input — the patched blob."},
                "operations_applied": {"type": "integer", "description": "Number of patch operations applied."},
                "size_bytes": {"type": "integer", "description": "Size of the patched JSON in bytes."},
            },
        },
    },
    "query_asset": {
        "fn": query_asset,
        "description": (
            "Apply a JSONPath expression to any blob asset and return only the matching values. "
            "Use instead of read_asset when you need a targeted subset of a large result blob "
            "(e.g. clip URLs, segment timestamps). "
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
                        "'$.clip_urls[*]' or '$.segments[*].start_seconds'."
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
            "Apply a JSONPath expression to any blob asset and return only the matching values. "
            "Writes the results to a new asset blob. Use to pass as frames_asset to detect_* tools."
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
                "video_url": {"type": "string", "minLength": 1},
                "job_id": {"type": "string", "minLength": 1},
                "session_id": {"type": "string"},                
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
                        "'$.segments[*]'."
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
                #"matches": {"type": "array"},
                "total_matches": {"type": "integer"},
                "truncated": {"type": "boolean"},
            },
        },
    }
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
