#!/usr/bin/env bash
# scripts/bootstrap-dev.sh  (gitignored — never commit)
#
# One-time provisioning of the video-extract-dev Azure environment.
# Fill in every value marked FILL_IN before running.
#
# Usage:
#   bash scripts/bootstrap-dev.sh

set -euo pipefail

# ─── Credentials ──────────────────────────────────────────────────────────────

# shellcheck source=credentials.sh
source "$(dirname "${BASH_SOURCE[0]}")/credentials.sh"

# ─── Run ──────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../infrastructure/terraform/envs/dev"

# ─── Logging setup ────────────────────────────────────────────────────────────
LOG_DIR="$SCRIPT_DIR/../gitlab-logs/terraform"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/bootstrap-$(date +%Y%m%d_%H%M%S).log"
echo "Logging to $LOG_FILE"

# Wrapper: run a command, tee stdout+stderr to log file, preserve exit code
tf() { "$@" 2>&1 | tee -a "$LOG_FILE"; }

echo "Initialising Terraform..."
cd "$TF_DIR"
tf terraform init \
  -backend-config="access_key=$TF_STATE_ACCESS_KEY" \
  -reconfigure \
  -input=false

# ─── Phase 1: create ACR only ─────────────────────────────────────────────────
# Container apps require images to exist in ACR at creation time.
# We create the ACR first, push placeholders, then apply the rest.

echo ""
echo "Phase 1: Creating ACR..."
tf terraform apply -target=module.acr -auto-approve

echo ""
echo "Pushing placeholder images to ACR..."
ACR_NAME=$(az acr list --resource-group video-extract-dev --query "[0].name" --output tsv)
ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --resource-group video-extract-dev --query loginServer --output tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --resource-group video-extract-dev --query "passwords[0].value" --output tsv)
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --resource-group video-extract-dev --query username --output tsv)

PLACEHOLDER="mcr.microsoft.com/azuredocs/containerapps-helloworld:latest"
docker pull "$PLACEHOLDER"
echo "$ACR_PASSWORD" | docker login "$ACR_LOGIN_SERVER" -u "$ACR_USERNAME" --password-stdin
for svc in api-gateway agent-orchestrator preprocessing-worker notification-worker \
           mcp-server-analysis mcp-server-processing angular-shell librechat; do
  docker tag "$PLACEHOLDER" "$ACR_LOGIN_SERVER/$svc:latest"
  docker push "$ACR_LOGIN_SERVER/$svc:latest"
done

# ─── Phase 2: full apply ──────────────────────────────────────────────────────

echo ""
echo "Phase 2: Full terraform apply..."
tf terraform apply -auto-approve
