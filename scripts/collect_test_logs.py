"""
Fetch per-test action logs from the API gateway and write them to ci-logs/tests/.

Called by collect_e2e_logs CI job after e2e_tests completes.

Environment variables:
  API_GW_URL  — base URL of the API gateway (e.g. https://api-gateway.example.com)

Input:
  ci-logs/test-job-ids.json  — written by pytest_sessionfinish in tests/e2e/conftest.py
                                 mapping test node id → list of job UUIDs

Output:
  ci-logs/tests/<safe_test_name>.log  — one file per test, containing the full
                                         action log for every job created by that test
"""
import json
import os
import sys
import urllib.request

api_gw = os.environ.get("API_GW_URL", "").rstrip("/")
if not api_gw or not api_gw.startswith("https://") or len(api_gw) <= len("https://"):
    print(
        f"ERROR: API_GW_URL is not set or has no hostname ({api_gw!r}). "
        "Ensure the collect_e2e_logs CI job exports API_GW_URL before calling this script.",
        file=sys.stderr,
    )
    sys.exit(1)

auth = "Bearer local-dev-skip-auth"

with open("ci-logs/test-job-ids.json") as f:
    registry = json.load(f)

for test_name, job_ids in registry.items():
    safe_name = test_name.replace("/", "__").replace("::", "__")
    out_path = f"ci-logs/tests/{safe_name}.log"
    with open(out_path, "w") as out:
        out.write(f"=== {test_name} ===\n\n")
        for job_id in job_ids:
            out.write(f"--- Job {job_id} ---\n")
            try:
                req = urllib.request.Request(
                    f"{api_gw}/v1/jobs/{job_id}/logs",
                    headers={"Authorization": auth},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                logs = data.get("logs", [])
                for entry in logs:
                    ts = entry.get("created_at", "")[:19]
                    log_type = entry.get("log_type", "")
                    tool = entry.get("tool_name") or ""
                    agent = entry.get("agent_role") or ""
                    msg = (entry.get("message") or "").strip()
                    err = entry.get("error_text") or ""
                    prefix = f"[{ts}] [{log_type}]"
                    if tool:
                        prefix += f" tool={tool}"
                    if agent:
                        prefix += f" agent={agent}"
                    out.write(f"{prefix}\n")
                    if msg:
                        for line in msg.splitlines():
                            out.write(f"  {line}\n")
                    if err:
                        out.write(f"  ERROR: {err}\n")
                    out.write("\n")
                if not logs:
                    out.write("  (no logs)\n")
            except Exception as e:
                out.write(f"  [failed to fetch logs: {e}]\n")
            out.write("\n")
    print(f"  wrote {out_path}")
