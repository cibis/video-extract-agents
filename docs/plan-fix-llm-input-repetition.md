# Fix: LLM Input Repetition — Inject Instructions Once, Reference on Repeat Calls

## Context

Batch-size guidance, the `write_query_asset` 3-step pipeline, and the `extract_frames` ordering
requirement each appear **4–10+ times** in the LLM log for a single job.

Root causes (from `docs/llm-input-repetition-analysis.md`):
1. `_build_description()` renders `input_schema` parameter descriptions into the tool's
   system-prompt entry — repeated verbatim for every tool, on every ReAct turn.
2. The write_query_asset pipeline block is **identical** in both `make_plan_task()` and
   `make_analysis_task()`. The analyst already receives the plan as context.
3. The extract_frames MANDATORY FIRST STEP is in both the **backstory** and the **task description**.
4. Batch-size numbers are in the **backstory**, the **task description**, and **two tool base
   descriptions** (`detect_motion`, `detect_motion_sports`).

---

## Approach

Remove each instruction from every location except its single authoritative home.
Then add a one-time "repeated tool call" directive so the LLM references previous calls
rather than re-explaining itself when the same tool is invoked again (e.g. multi-video jobs).

| Instruction | Single authoritative home |
|---|---|
| Batch sizes (50–100 / 20) | Agent backstory (`agents.py`) — system prompt, potentially cached |
| write_query_asset pipeline | Planner task description (`tasks.py:make_plan_task`) — analyst gets the plan as context |
| extract_frames first | Agent backstory (`agents.py`) — system prompt |
| Parameter cross-refs ("pass result_asset as frames_asset to…") | Tool base description only — not duplicated in parameter descriptions |

---

## Changes

### 1. `backend/agent-orchestrator/app/tools/crewai_tools.py` — `_build_description()` (line 337)

**Biggest win.** Drop the `fdesc` from every field line.
Parameter descriptions like "Frames to process per batch. 50–100 is safe for memory…"
currently appear in the system prompt for every tool on every ReAct turn.
That guidance lives in the backstory — the tool schema only needs field name + type.

```python
# Before (line 337):
field_lines.append(f"  {field}: {ftype}{req} — {fdesc}" if fdesc else f"  {field}: {ftype}{req}")

# After:
field_lines.append(f"  {field}: {ftype}{req}")
```

### 2. `mcp-servers/mcp-server-analysis/app/tool_registry.py`

Remove the trailing batch-size sentence from the **base description** of two tools
(the base description still goes into the system prompt; removing it here avoids
duplicating guidance now only in the backstory).

- **`detect_motion` description** (line ~124–128): Remove sentence
  `"Use frame_batch_size matching the total frame count for short clips, or 50–100 for longer videos."`

- **`detect_motion_sports` description** (line ~184–188): Same removal.

All other tool descriptions — keep as-is.

### 3. `backend/agent-orchestrator/app/tasks.py` — `make_analysis_task()`

Three removals + one addition:

**a) Remove the write_query_asset 3-step pipeline block** (lines ~278–291)
```
"  - Frontier tools (analyze_scene, detect_objects_vision) cost per batch. …
 The mandatory pipeline before any frontier tool call is:
      1. Run a free detection tool …
      2. Call write_query_asset …
      3. Pass write_query_asset.result_asset as frames_asset …"
```
This is verbatim identical to the block in `make_plan_task()`. The analyst receives
the planner's output as context — the plan already contains these steps.

**b) Remove MANDATORY FIRST STEP block** (lines ~297–301)
```
"MANDATORY FIRST STEP: For each video, call extract_frames with keyframe_index_asset …"
```
Already in the analysis agent backstory (MANDATORY PIPELINE — extract_frames FIRST, lines ~168–178).
Having it in both the system prompt and the human turn means it appears twice per LLM call.

**c) Remove the batch-size parenthetical** from the COST CONSTRAINTS line (line ~274–275)
```
"(50–100 for CV tools; 20 per batch for estimate_height_above_surface)"
```
Already in the agent backstory (BATCH EFFICIENCY, lines ~165–167). Keep only the free/frontier
cost tier distinction in the task-level COST CONSTRAINTS (that sentence is not in the backstory).

**d) Add REPEATED TOOL CALLS instruction** (after the existing COST CONSTRAINTS block)
```python
"REPEATED TOOL CALLS: When you call the same tool more than once in this task "
"(e.g. extract_frames or detect_* for each of multiple videos), write "
"'Applying same parameters as previous [tool_name] call above.' in your Thought "
"for the second and any further calls to the same tool. "
"Do not re-state batch sizes, pipeline steps, or rationale already given.\n\n"
```
This instructs the LLM to reference earlier context instead of expanding the same
guidance again into the growing conversation history.

---

## Files Modified

| File | Change |
|---|---|
| `backend/agent-orchestrator/app/tools/crewai_tools.py` | Strip `fdesc` from `_build_description()` field lines |
| `mcp-servers/mcp-server-analysis/app/tool_registry.py` | Remove batch-size sentence from `detect_motion` + `detect_motion_sports` base descriptions |
| `backend/agent-orchestrator/app/tasks.py` | Remove write_query_asset pipeline, MANDATORY FIRST STEP, and batch-size parenthetical from `make_analysis_task()`; add REPEATED TOOL CALLS instruction |

`backend/agent-orchestrator/app/agents.py` — **no changes**. The backstory is the single
authoritative home for batch sizes and the extract_frames ordering rule.

---

## What Is Deliberately Kept (Not Removed)

- **write_query_asset pipeline** stays in `make_plan_task()` — the planner needs it.
- **MANDATORY PIPELINE** stays in the analysis agent backstory — correct place for cross-job consistency.
- **BATCH EFFICIENCY paragraph** stays in the analysis agent backstory — single source of truth.
- **COST CONSTRAINTS free/frontier tier note** stays in `make_analysis_task()` — the backstory's
  phrasing is slightly different; this reminder is worth keeping since frontier tool cost is
  per-call and the analyst decides when to invoke them.
- All `output_schema` descriptions in `tool_registry.py` — unchanged; they're not rendered
  into the LLM prompt.
- `detect_objects.frame_batch_size.description` (already "Default 50." — already short) —
  no change needed.

---

## Verification

1. Run a job with `LOG_LEVEL=DEBUG`.
2. In the `job_steps` table (written by `litellm_callbacks.py`), inspect the Input rows
   for the analysis agent.
3. Confirm `"50–100 is safe for memory"` appears in the system prompt (backstory) only —
   **not** in the tool schema block and **not** in the task description.
4. Confirm the write_query_asset pipeline block appears exactly once in the full conversation
   (in the planner turn, via the plan context), not again in the analysis task prompt.
5. For a multi-video job: confirm the analysis agent's second `extract_frames` Thought contains
   "Applying same parameters as previous" rather than re-stating batch sizes.
