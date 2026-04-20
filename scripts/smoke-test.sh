#!/usr/bin/env bash
# smoke-test.sh — Verify a deployed environment is healthy before or after production approval.
# Usage: scripts/smoke-test.sh [dev|prod]
#
# Requires: az CLI authenticated (az login or service principal env vars set)
#           jq, curl
#
# Exit code 0 = all checks passed.
# Exit code 1 = one or more checks failed.

set -euo pipefail

ENV=${1:-dev}
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
  echo "Usage: $0 [dev|prod]" >&2
  exit 1
fi

RG="video-extract-$ENV"
FAILED=0

pass() { echo "  [OK]   $1"; }
fail() { echo "  [FAIL] $1 — $2"; FAILED=1; }

echo "=== Smoke test: $ENV ($RG) ==="

# ── Resolve FQDNs ──────────────────────────────────────────────────────────────
get_fqdn() {
  az containerapp show --name "$1" --resource-group "$RG" \
    --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null || true
}

GW_FQDN=$(get_fqdn api-gateway)
ORC_FQDN=$(get_fqdn agent-orchestrator)
MCA_FQDN=$(get_fqdn mcp-server-analysis)
MCP_FQDN=$(get_fqdn mcp-server-processing)

# ── API Gateway ────────────────────────────────────────────────────────────────
echo ""
echo "--- API Gateway ---"
if [[ -z "$GW_FQDN" ]]; then
  fail "api-gateway reachable" "could not resolve FQDN from $RG"
else
  HEALTH=$(curl -sf --max-time 10 "https://$GW_FQDN/health" 2>/dev/null || echo "{}")
  GW_STATUS=$(echo "$HEALTH" | jq -r '.status // "missing"' 2>/dev/null || echo "parse error")
  [[ "$GW_STATUS" == "ok" ]] && pass "api-gateway /health → status: ok" \
    || fail "api-gateway /health → status" "got: $GW_STATUS"
fi

# ── Agent Orchestrator ────────────────────────────────────────────────────────
echo ""
echo "--- Agent Orchestrator ---"
if [[ -z "$ORC_FQDN" ]]; then
  fail "agent-orchestrator reachable" "could not resolve FQDN from $RG"
else
  HEALTH=$(curl -sf --max-time 10 "https://$ORC_FQDN/health" 2>/dev/null || echo "{}")
  ORC_STATUS=$(echo "$HEALTH" | jq -r '.status // "missing"' 2>/dev/null || echo "parse error")
  [[ "$ORC_STATUS" == "ok" ]] && pass "agent-orchestrator /health → status: ok" \
    || fail "agent-orchestrator /health → status" "got: $ORC_STATUS"
fi

# ── MCP Server Analysis ────────────────────────────────────────────────────────
echo ""
echo "--- MCP Server Analysis ---"
if [[ -z "$MCA_FQDN" ]]; then
  fail "mcp-server-analysis reachable" "could not resolve FQDN from $RG"
else
  TOOL_COUNT=$(curl -sf --max-time 15 "https://$MCA_FQDN/tools" 2>/dev/null \
    | jq 'length' 2>/dev/null || echo 0)
  [[ "$TOOL_COUNT" -ge 12 ]] && pass "mcp-server-analysis /tools returns $TOOL_COUNT tools (≥12)" \
    || fail "mcp-server-analysis /tools" "got $TOOL_COUNT tools, expected ≥12"
fi

# ── MCP Server Processing ──────────────────────────────────────────────────────
echo ""
echo "--- MCP Server Processing ---"
if [[ -z "$MCP_FQDN" ]]; then
  fail "mcp-server-processing reachable" "could not resolve FQDN from $RG"
else
  TOOL_COUNT=$(curl -sf --max-time 15 "https://$MCP_FQDN/tools" 2>/dev/null \
    | jq 'length' 2>/dev/null || echo 0)
  [[ "$TOOL_COUNT" -ge 7 ]] && pass "mcp-server-processing /tools returns $TOOL_COUNT tools (≥7)" \
    || fail "mcp-server-processing /tools" "got $TOOL_COUNT tools, expected ≥7"
fi

# ── Service Bus dead-letter counts ─────────────────────────────────────────────
echo ""
echo "--- Service Bus ---"
SB_NS=$(az servicebus namespace list --resource-group "$RG" \
  --query "[0].name" -o tsv 2>/dev/null || true)
if [[ -z "$SB_NS" ]]; then
  fail "Service Bus namespace" "not found in $RG"
else
  for q in video-uploaded video-indexed job-queued; do
    DL=$(az servicebus queue show \
      --namespace-name "$SB_NS" --resource-group "$RG" --name "$q" \
      --query "countDetails.deadLetterMessageCount" -o tsv 2>/dev/null || echo "?")
    [[ "$DL" == "0" ]] && pass "queue $q: dead-letter count = 0" \
      || fail "queue $q: dead-letter count" "$DL (expected 0)"
  done
fi

# ── Container Apps — at least 1 running replica each ──────────────────────────
echo ""
echo "--- Container Apps ---"
SERVICES=(api-gateway agent-orchestrator preprocessing-worker
          mcp-server-analysis mcp-server-processing angular-shell librechat)
for svc in "${SERVICES[@]}"; do
  RUNNING=$(az containerapp replica list --name "$svc" --resource-group "$RG" \
    --query "length([?properties.runningState=='Running'])" -o tsv 2>/dev/null || echo 0)
  [[ "$RUNNING" -ge 1 ]] && pass "$svc: $RUNNING running replica(s)" \
    || fail "$svc: running replicas" "$RUNNING (expected ≥1)"
done

# ── Result ─────────────────────────────────────────────────────────────────────
echo ""
if [[ $FAILED -eq 0 ]]; then
  echo "All checks passed — $ENV is healthy."
  exit 0
else
  echo "One or more checks FAILED. Do not proceed with production approval."
  exit 1
fi
