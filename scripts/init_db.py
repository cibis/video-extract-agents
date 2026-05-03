#!/usr/bin/env python3
"""
Create all database tables for the Video Extract Project.
Usage:
  python scripts/init_db.py
  python scripts/init_db.py --drop   # drop and recreate all tables
"""
import argparse
import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/videoextract",
)

DROP_TABLES_SQL = """
DROP TABLE IF EXISTS tool_call_cache CASCADE;
DROP TABLE IF EXISTS tool_progress CASCADE;
DROP TABLE IF EXISTS job_logs CASCADE;
DROP TABLE IF EXISTS session_assets CASCADE;
DROP TABLE IF EXISTS assets CASCADE;
DROP TABLE IF EXISTS outputs CASCADE;
DROP TABLE IF EXISTS job_steps CASCADE;
DROP TABLE IF EXISTS jobs CASCADE;
DROP TABLE IF EXISTS video_keyframe_index CASCADE;
DROP TABLE IF EXISTS videos CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS model_context_windows CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
"""

CREATE_TABLES_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Sessions: one per conversation thread, groups all uploads + jobs + outputs
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    original_url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS videos_user_id_idx ON videos(user_id);
CREATE INDEX IF NOT EXISTS videos_session_id_idx ON videos(session_id);
CREATE INDEX IF NOT EXISTS videos_status_idx ON videos(status);

CREATE TABLE IF NOT EXISTS video_keyframe_index (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
    frame_index INTEGER NOT NULL,
    frame_url TEXT NOT NULL,
    timestamp_seconds FLOAT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(video_id, frame_index)
);

CREATE INDEX IF NOT EXISTS keyframe_video_id_idx ON video_keyframe_index(video_id);

-- Non-video and generated assets (JSON, text, images, etc.)
CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    blob_url TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    file_size_bytes BIGINT,
    source TEXT NOT NULL DEFAULT 'upload',   -- 'upload' | 'generated'
    source_job_id UUID,                       -- set when source='generated'
    description TEXT,                         -- human-readable description of asset content
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS assets_user_id_idx ON assets(user_id);
CREATE INDEX IF NOT EXISTS assets_session_id_idx ON assets(session_id);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    parent_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    video_id UUID REFERENCES videos(id) ON DELETE SET NULL,   -- backward-compat: first video
    video_ids UUID[],                                          -- all videos for this job
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    output_url TEXT,        -- backward-compat: first output URL
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS jobs_user_id_idx ON jobs(user_id);
CREATE INDEX IF NOT EXISTS jobs_session_id_idx ON jobs(session_id);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);

CREATE TABLE IF NOT EXISTS job_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS job_steps_job_id_idx ON job_steps(job_id);

CREATE TABLE IF NOT EXISTS outputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    blob_url TEXT NOT NULL,
    filename TEXT,
    content_type TEXT DEFAULT 'video/mp4',
    signed_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS outputs_job_id_idx ON outputs(job_id);
CREATE INDEX IF NOT EXISTS outputs_session_id_idx ON outputs(session_id);

-- Unified index of every blob associated with a session (uploads + generated)
CREATE TABLE IF NOT EXISTS session_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL,   -- 'uploaded_video'|'uploaded_file'|'job_output_video'|'job_output_file'|'segment'
    blob_url TEXT NOT NULL,
    filename TEXT,
    content_type TEXT,
    source_id UUID,             -- references videos.id, assets.id, or outputs.id
    label TEXT,
    metadata_json JSONB,
    description TEXT,           -- human-readable description of asset content
    summary_json JSONB,         -- structured summary returned by the tool that produced this asset
    source_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,  -- job that created this asset
    session_hidden BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS session_assets_session_id_idx ON session_assets(session_id);
CREATE INDEX IF NOT EXISTS session_assets_asset_type_idx ON session_assets(asset_type);
CREATE UNIQUE INDEX IF NOT EXISTS session_assets_session_source_idx ON session_assets(session_id, source_id);

-- Per-job activity log: LLM calls, MCP tool calls, agent steps, and errors across all containers
CREATE TABLE IF NOT EXISTS job_logs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID        REFERENCES jobs(id) ON DELETE CASCADE,
    session_id    UUID        REFERENCES sessions(id) ON DELETE SET NULL,
    service_name  TEXT        NOT NULL DEFAULT '',
    log_type      TEXT        NOT NULL,   -- 'llm_call' | 'tool_call' | 'agent_step' | 'task_complete' | 'error'
    model_id      TEXT,
    tool_name     TEXT,
    agent_role    TEXT,
    task_name     TEXT,
    message       TEXT,
    message_type  TEXT        NOT NULL DEFAULT 'Output',  -- 'Input' | 'Output' | 'Error'
    call_group_id UUID        NOT NULL DEFAULT gen_random_uuid(),
    sequence_num  INTEGER     NOT NULL DEFAULT 0,
    error_text    TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS job_logs_job_id_idx        ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS job_logs_session_id_idx    ON job_logs(session_id);
CREATE INDEX IF NOT EXISTS job_logs_call_group_id_idx ON job_logs(call_group_id);
CREATE INDEX IF NOT EXISTS job_logs_sequence_num_idx  ON job_logs(job_id, sequence_num);

-- Per-tool-call progress tracking for real-time progress bars in the UI
CREATE TABLE IF NOT EXISTS tool_progress (
    call_group_id   UUID         PRIMARY KEY,
    job_id          UUID         REFERENCES jobs(id) ON DELETE CASCADE,
    tool_name       TEXT         NOT NULL,
    total_units     INTEGER,
    processed_units INTEGER      NOT NULL DEFAULT 0,
    unit_label      TEXT         NOT NULL DEFAULT 'items',
    status          TEXT         NOT NULL DEFAULT 'running',
    started_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS tool_progress_job_id_idx     ON tool_progress(job_id);
CREATE INDEX IF NOT EXISTS tool_progress_updated_at_idx ON tool_progress(job_id, updated_at);

-- Per-user MCP tool call cache (keyed by user_id + tool_name + input_hash, excluding job_id/session_id)
CREATE TABLE IF NOT EXISTS tool_call_cache (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name   TEXT        NOT NULL,
    input_hash  TEXT        NOT NULL,
    input_json  JSONB       NOT NULL,
    output_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS tool_call_cache_lookup_idx ON tool_call_cache (user_id, tool_name, input_hash);
CREATE INDEX IF NOT EXISTS tool_call_cache_user_id_idx ON tool_call_cache (user_id);

-- Platform configuration settings (model names, etc.)
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Local development seed user (matches LOCAL_DEV_SKIP_AUTH identity in api-gateway)
INSERT INTO users (id, email) VALUES
    ('00000000-0000-0000-0000-000000000001', 'dev@local')
ON CONFLICT (id) DO NOTHING;

-- Context window sizes and safety margins for vision model batching.
-- model_name matches the full LiteLLM model string used in tool_frontier_model.
-- Read by process_frames_in_batches on every call (no caching).
CREATE TABLE IF NOT EXISTS model_context_windows (
    model_name            TEXT PRIMARY KEY,
    context_window_tokens INTEGER NOT NULL,
    safety_margin         FLOAT NOT NULL DEFAULT 0.5,
    compression_threshold FLOAT NOT NULL DEFAULT 0.7,
    description           TEXT,
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO model_context_windows (model_name, context_window_tokens, safety_margin, compression_threshold, description) VALUES
    ('anthropic/claude-opus-4-6',                 200000, 0.5, 0.7, 'Claude Opus 4.6 (direct Anthropic API)'),
    ('anthropic/claude-sonnet-4-6',               200000, 0.5, 0.7, 'Claude Sonnet 4.6 (direct Anthropic API)'),
    ('anthropic/claude-haiku-4-5-20251001',       200000, 0.5, 0.7, 'Claude Haiku 4.5 (direct Anthropic API)'),
    ('openai/gpt-4o',                             128000, 0.5, 0.7, 'GPT-4o (OpenAI API)'),
    ('openai/gpt-4o-mini',                        128000, 0.5, 0.7, 'GPT-4o Mini (OpenAI API)'),
    ('bedrock/us.amazon.nova-2-lite-v1:0',        300000, 0.7, 0.08, 'Amazon Nova 2 Lite (Bedrock)'),
    ('bedrock/us.anthropic.claude-opus-4-5-v1:0', 200000, 0.5, 0.7, 'Claude Opus 4.5 (Bedrock)'),
    ('bedrock/openai.gpt-oss-120b-1:0',           128000, 0.5, 0.7, 'GPT OSS 120B (Bedrock)')
ON CONFLICT (model_name) DO NOTHING;

INSERT INTO app_settings (key, value, description) VALUES
    ('agent_model',                'bedrock/us.amazon.nova-2-lite-v1:0',   'LiteLLM model string for CrewAI agents'),
    ('tool_frontier_model',        'bedrock/us.amazon.nova-2-lite-v1:0', 'LiteLLM model string for vision tools in mcp-server-analysis'),
    ('agent_rpm_limit',            '16',                                  'Max LLM requests per minute for agent calls (empty = no limit; default: 4 = 1 call per 15 s)'),
    ('tool_rpm_limit',             '16',                                  'Max frontier model requests per minute for tool calls (empty = no limit; default: 4 = 1 call per 15 s)'),
    ('keyframe_fps',               '1.5',                                'Frames per second for periodic keyframe extraction in preprocessing-worker (default: 1.5)'),
    ('keyframe_scene_threshold',   '0.2',                                'FFmpeg scene-change detection threshold 0–1; lower = more sensitive (default: 0.2)'),
    ('planner_agent_model',        'anthropic/claude-haiku-4-5-20251001',          'LiteLLM model string for the Planner agent (overrides agent_model; empty = use agent_model)'),
    ('planner_agent_rpm_limit',    '2',                                  'Max LLM requests per minute for the Planner agent (overrides agent_rpm_limit; empty = use agent_rpm_limit)'),
    ('tool_max_retry_limit',       '5',                                   'Max consecutive ToolUsageErrors per tool per job before the task is aborted (default: 3)')
ON CONFLICT (key) DO NOTHING;
"""


MIGRATE_SQL = """
-- Schema migrations: add new tables and columns if they don't exist yet.
-- Safe to run on both fresh and existing databases.

-- tool_call_cache: per-user MCP tool result cache
CREATE TABLE IF NOT EXISTS tool_call_cache (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name   TEXT        NOT NULL,
    input_hash  TEXT        NOT NULL,
    input_json  JSONB       NOT NULL,
    output_json JSONB       NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS tool_call_cache_lookup_idx ON tool_call_cache (user_id, tool_name, input_hash);
CREATE INDEX IF NOT EXISTS tool_call_cache_user_id_idx ON tool_call_cache (user_id);

-- model_context_windows: per-model context window sizes for vision batching and context compression.
CREATE TABLE IF NOT EXISTS model_context_windows (
    model_name            TEXT PRIMARY KEY,
    context_window_tokens INTEGER NOT NULL,
    safety_margin         FLOAT NOT NULL DEFAULT 0.5,
    compression_threshold FLOAT NOT NULL DEFAULT 0.7,
    description           TEXT,
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE model_context_windows
    ADD COLUMN IF NOT EXISTS compression_threshold FLOAT NOT NULL DEFAULT 0.7;

INSERT INTO model_context_windows (model_name, context_window_tokens, safety_margin, compression_threshold, description) VALUES
    ('anthropic/claude-opus-4-6',                 200000, 0.5, 0.7, 'Claude Opus 4.6 (direct Anthropic API)'),
    ('anthropic/claude-sonnet-4-6',               200000, 0.5, 0.7, 'Claude Sonnet 4.6 (direct Anthropic API)'),
    ('anthropic/claude-haiku-4-5-20251001',       200000, 0.5, 0.7, 'Claude Haiku 4.5 (direct Anthropic API)'),
    ('openai/gpt-4o',                             128000, 0.5, 0.7, 'GPT-4o (OpenAI API)'),
    ('openai/gpt-4o-mini',                        128000, 0.5, 0.7, 'GPT-4o Mini (OpenAI API)'),
    ('bedrock/us.amazon.nova-2-lite-v1:0',        300000, 0.7, 0.08, 'Amazon Nova 2 Lite (Bedrock)'),
    ('bedrock/us.anthropic.claude-opus-4-5-v1:0', 200000, 0.5, 0.7, 'Claude Opus 4.5 (Bedrock)'),
    ('bedrock/openai.gpt-oss-120b-1:0',           128000, 0.5, 0.7, 'GPT OSS 120B (Bedrock)')
ON CONFLICT (model_name) DO UPDATE SET
    context_window_tokens = EXCLUDED.context_window_tokens,
    safety_margin         = EXCLUDED.safety_margin,
    compression_threshold = EXCLUDED.compression_threshold,
    description           = EXCLUDED.description,
    updated_at            = NOW();

ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS description TEXT;

ALTER TABLE session_assets
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS summary_json JSONB,
    ADD COLUMN IF NOT EXISTS source_job_id UUID REFERENCES jobs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS session_assets_source_job_id_idx ON session_assets(source_job_id);

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE job_logs
    ADD COLUMN IF NOT EXISTS cached BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE session_assets
    ADD COLUMN IF NOT EXISTS session_hidden BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS session_assets_hidden_idx ON session_assets(session_id, session_hidden);
"""


async def main(drop: bool) -> None:
    url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    print(f"Connecting to: {url}")
    conn = await asyncpg.connect(url, ssl=False, timeout=10)
    try:
        if drop:
            print("Dropping all tables...")
            await conn.execute(DROP_TABLES_SQL)
            print("Tables dropped.")
        print("Creating tables...")
        await conn.execute(CREATE_TABLES_SQL)
        print("Applying schema migrations...")
        await conn.execute(MIGRATE_SQL)
        print("All tables created successfully.")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialise database schema")
    parser.add_argument("--drop", action="store_true", help="Drop and recreate all tables")
    args = parser.parse_args()
    asyncio.run(main(args.drop))
