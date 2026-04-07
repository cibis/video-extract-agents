"agent_model"	"bedrock/us.amazon.nova-2-lite-v1:0"
"agent_rpm_limit"	"16"
"keyframe_fps"	"1.5"
"keyframe_scene_threshold"	"0.2"
"planner_agent_model"	"anthropic/claude-haiku-4-5-20251001"
"planner_agent_rpm_limit"	"2"
"tool_frontier_model"	"bedrock/us.amazon.nova-2-lite-v1:0"
"tool_max_retry_limit"	"10"
"tool_rpm_limit"	"16"

```
Extract from this kitesurfer first person video taken with a helmet mounted camera all segments with the kitesurfer jumping
```

Because weaker models keep including segments on land, had to specify the "water/ocean/sea/waves" part:
```
Extract from this kitesurfer first person video taken with a helmet mounted camera all segments with the kitesurfer jumping above water/ocean/sea/waves
```