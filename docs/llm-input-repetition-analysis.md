# Why Batch-Size Guidance Repeats in LLM Inputs

## Problem

When inspecting job logs / LLM input traces, the string
`"Frames to process per batch. 50–100 is safe for memory; use total frame count for short clips."`
(and similar batch-size guidance) appears many times in every analysis-agent turn,
wasting tokens and inflating cost.

---

## Root Causes

There are **four independent sources** that all inject the same guidance into the LLM context,
and CrewAI's ReAct loop **multiplies the repetition** across turns.

### 1. Tool parameter schemas — `mcp-servers/mcp-server-analysis/app/tool_registry.py`

The exact string appears **twice in tool schemas**, once per tool:

| Tool | Field | Lines |
|---|---|---|
| `detect_motion` | `frame_batch_size.description` | 148–149 |
| `detect_motion_sports` | `frame_batch_size.description` | 208–209 |

CrewAI converts all registered tools into JSON tool definitions and prepends them to the
**system prompt** of every LLM call. Because both tools carry identical descriptions for
`frame_batch_size`, the string appears **twice per LLM call** just from tool schemas.

### 2. Analysis agent backstory — `backend/agent-orchestrator/app/agents.py`

`make_analysis_agent()` backstory (line 165–166):
```
"BATCH EFFICIENCY: For free CV tools (OpenCV, YOLO) use 50–100 frames per batch
 or the full frame count for short clips."
```

This is the agent's **system prompt** and is sent on **every** LLM call for the analysis agent —
third copy of the same guidance.

### 3. Analysis task description — `backend/agent-orchestrator/app/tasks.py`

`make_analysis_task()` description (line 274–275):
```
"(50–100 for CV tools; 20 per batch for estimate_height_above_surface)"
```

This lands in the **human turn** of the first LLM call — fourth copy.

### 4. Planner context re-emitted to analysis agent

`make_plan_task()` (lines 163–165) includes cost-constraint prose. The planner's output
(the plan) is passed as context to the analysis task, so the analysis agent also receives
the planner's restatement of the same advice.

---

## Why It Repeats *Many* Times in the Log

CrewAI runs the analysis agent in a **ReAct loop** — one LLM call per tool invocation.
A typical job calls `extract_frames` → `detect_motion` → `write_query_asset` → `analyze_scene`
= **4+ LLM calls** for the analysis agent alone.

Each call includes:
- Full system prompt (backstory with batch-size guidance)
- Full tool list (both `detect_motion` and `detect_motion_sports` schemas with identical descriptions)
- Accumulated conversation history (prior tool calls + observations)

So for a 4-tool analysis run, the batch-size string appears **~10+ times** in the full log.

---

## Secondary Issue: Duplicated write_query_asset Pipeline Instructions

The mandatory `write_query_asset` pipeline (run free tool → filter with write_query_asset →
pass filtered frames_asset to frontier tool) is written out verbatim in **both**:
- `make_plan_task()` description — `tasks.py` lines 168–183
- `make_analysis_task()` description — `tasks.py` lines 278–291

These are identical multi-line blocks that both end up in the analysis agent's context
(planner output → analysis task context).

---

## Files to Fix

| File | Issue | Suggested Fix |
|---|---|---|
| `mcp-servers/mcp-server-analysis/app/tool_registry.py` L148–149, 208–209 | `detect_motion` and `detect_motion_sports` have identical `frame_batch_size` descriptions | Shorten both to e.g. `"Batch size (1–500, default 50)."` — detail belongs in the backstory, not repeated per-tool |
| `backend/agent-orchestrator/app/agents.py` L165–167 | BATCH EFFICIENCY paragraph in backstory | Remove — the tool schema already carries it |
| `backend/agent-orchestrator/app/tasks.py` L274–275 | Inline batch-size hint in analysis task description | Remove — already in tool schema |
| `backend/agent-orchestrator/app/tasks.py` L278–291 | write_query_asset pipeline block duplicated from plan task | Remove from analysis task — the plan (planner output) already instructs the analyst |

---

## Verification

After changes, run a job with `LOG_LEVEL=DEBUG` and inspect the LLM call records in
`job_steps` (written by `litellm_callbacks.py`). The batch-size string should appear
**at most once per LLM call** (in a single tool schema), and zero times in backstory/task prose.
