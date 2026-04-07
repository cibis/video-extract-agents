"""Unit tests for app.tools.catalogue."""
import pytest

from app.tools.catalogue import (
    EXTERNAL_AGENT_ONLY_TOOLS,
    filter_catalogue_for_frontend,
)


_SAMPLE_CATALOGUE = [
    {"name": "extract_frames", "server": "analysis"},
    {"name": "detect_motion", "server": "analysis"},
    {"name": "ingest_video", "server": "analysis"},
    {"name": "split_video", "server": "processing"},
    {"name": "merge_clips", "server": "processing"},
]


def test_filter_removes_ingest_video():
    result = filter_catalogue_for_frontend(_SAMPLE_CATALOGUE)
    names = {t["name"] for t in result}
    assert "ingest_video" not in names


def test_filter_preserves_other_tools():
    result = filter_catalogue_for_frontend(_SAMPLE_CATALOGUE)
    names = {t["name"] for t in result}
    assert names == {"extract_frames", "detect_motion", "split_video", "merge_clips"}


def test_filter_empty_catalogue():
    assert filter_catalogue_for_frontend([]) == []


def test_filter_catalogue_with_no_external_tools():
    catalogue = [{"name": "extract_frames"}, {"name": "split_video"}]
    assert filter_catalogue_for_frontend(catalogue) == catalogue


def test_external_agent_only_tools_contains_ingest_video():
    assert "ingest_video" in EXTERNAL_AGENT_ONLY_TOOLS
