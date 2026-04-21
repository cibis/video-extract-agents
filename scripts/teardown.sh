#!/usr/bin/env bash
# scripts/teardown.sh
#
# Destroys the dev Terraform-managed environment.
#
# What it destroys:
#   - Resource group (video-extract-dev) and everything in it:
#       Azure Container Apps environment + all 8 container apps (incl. PostgreSQL)
#       PostgreSQL data (Azure Files volume)
#       Blob Storage account (all uploaded videos, keyframes, outputs, assets)
#       Service Bus namespace and all queues
#       Azure Container Registry and all Docker images
#       Application Insights + Log Analytics workspace
#       Azure Front Door CDN profile
#       Key Vault (soft-deleted)
#
# What it does NOT destroy (one-time manual setup from getting-started.md §5–6):
#   - Terraform state storage (getting-started.md §5.1):
#       resource group   terraform-state-rg
#       storage account  tfstatevideoextract  (container: tfstate)
#   - CI service principal: 'video-extract-ci' in Azure AD  (getting-started.md §3.5)
#   - Azure Entra External ID (getting-started.md §6):
#       External ID tenant
#       App registrations: video-extract-api, video-extract-spa
#       Magic link user flow
#
# Usage:
#   scripts/teardown.sh
#
# Credentials are pre-filled below (file is gitignored — never commit).
# Override any value by exporting the variable before running.
#
# See docs/azure-credentials.md for where to find each value.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ─── Credentials ──────────────────────────────────────────────────────────────

# shellcheck source=credentials.sh
source "$(dirname "${BASH_SOURCE[0]}")/credentials.sh"

# ─── Auth — map AZURE_* → ARM_* (mirrors .gitlab-ci.yml &terraform-setup) ────

export ARM_CLIENT_ID="$AZURE_CLIENT_ID"
export ARM_CLIENT_SECRET="$AZURE_CLIENT_SECRET"
export ARM_TENANT_ID="$AZURE_TENANT_ID"
export ARM_SUBSCRIPTION_ID="$AZURE_SUBSCRIPTION_ID"

# Terraform input variables.
# Sensitive values only need to satisfy the Terraform parser — they are not
# used in any Azure API calls at destruction time (Terraform reads state only).
export TF_VAR_subscription_id="$AZURE_SUBSCRIPTION_ID"
export TF_VAR_db_admin_password="$DB_ADMIN_PASSWORD"
export TF_VAR_image_tag="${TF_VAR_image_tag:-latest}"
export TF_VAR_anthropic_api_key="${TF_VAR_anthropic_api_key:-}"
export TF_VAR_openai_api_key="${TF_VAR_openai_api_key:-}"
export TF_VAR_aws_access_key_id="${TF_VAR_aws_access_key_id:-}"
export TF_VAR_aws_secret_access_key="${TF_VAR_aws_secret_access_key:-}"

# ─── Destroy function ─────────────────────────────────────────────────────────

destroy_env() {
  local env="$1"
  local tf_dir="$REPO_ROOT/infrastructure/terraform/envs/$env"
  local rg="video-extract-$env"

  echo ""
  echo "══════════════════════════════════════════════════════════════════════"
  printf "  Destroying: %-6s   resource group: %s\n" "$env" "$rg"
  echo "══════════════════════════════════════════════════════════════════════"
  echo ""
  echo "  This will permanently delete:"
  echo "    • All Azure Container Apps (8 services including PostgreSQL)"
  echo "    • All PostgreSQL data (Azure Files volume)"
  echo "    • Blob Storage account and all data (videos, keyframes, outputs)"
  echo "    • Service Bus namespace and all queues"
  echo "    • Azure Container Registry and all Docker images"
  echo "    • Application Insights + Log Analytics workspace"
  echo "    • Azure Front Door CDN profile"
  echo "    • Key Vault (see soft-delete note below)"
  echo "    • Resource group $rg"
  echo ""

  echo ""
  echo "  Initialising Terraform..."
  cd "$tf_dir"
  terraform init \
    -backend-config="access_key=$TF_STATE_ACCESS_KEY" \
    -reconfigure \
    -input=false

  echo ""
  echo "  Running terraform destroy..."
  terraform destroy -auto-approve

  echo ""
  echo "  ✓ $env environment destroyed."
}

# ─── Main ─────────────────────────────────────────────────────────────────────

destroy_env dev

echo ""
echo "══════════════════════════════════════════════════════════════════════"
echo "  Teardown complete."
echo ""
echo "  The following one-time manual resources were NOT destroyed"
echo "  (see getting-started.md §5-6):"
echo ""
echo "    Terraform state storage (§5.1):"
echo "      rg: terraform-state-rg"
echo "      storage account: tfstatevideoextract  (container: tfstate)"
echo ""
echo "    CI service principal (§3.5): video-extract-ci"
echo ""
echo "    Azure Entra External ID (§6):"
echo "      External ID tenant"
echo "      App registrations: video-extract-api, video-extract-spa"
echo "      Magic link user flow"
echo "══════════════════════════════════════════════════════════════════════"
