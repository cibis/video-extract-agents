"""Maps MCP tool names to their server URLs."""
from app.config import settings

TOOL_REGISTRY: dict[str, str] = {
    # Analysis tools (read-only)
    "extract_frames": settings.mcp_analysis_url,
    "detect_motion": settings.mcp_analysis_url,
    "detect_objects": settings.mcp_analysis_url,
    "transcribe_audio": settings.mcp_analysis_url,
    # Processing tools (produce artifacts)
    "split_video": settings.mcp_processing_url,
    "extract_clip": settings.mcp_processing_url,
    "merge_clips": settings.mcp_processing_url,
    "transform_video": settings.mcp_processing_url,
}


def get_tool_server_url(tool_name: str) -> str:
    url = TOOL_REGISTRY.get(tool_name)
    if not url:
        raise ValueError(f"Unknown MCP tool: {tool_name}")
    return url
