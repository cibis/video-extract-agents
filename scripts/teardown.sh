#!/usr/bin/env bash
# scripts/teardown.sh
#
# Destroys the dev and/or prod Terraform-managed environments.
#
# What it destroys (per environment):
#   - Resource group (video-extract-dev / video-extract-prod) and everything in it:
#       Azure Container Apps environment + all 9 container apps (incl. PostgreSQL)
#       PostgreSQL data (Azure Files volume)
#       Blob Storage account (all uploaded videos, keyframes, outputs, assets)
#       Service Bus namespace and all queues
#       Azure Container Registry and all Docker images
#       Application Insights + Log Analytics workspace
#       Azure Front Door CDN profile
#       Azure Communication Services
#       Key Vault (soft-deleted — see prod note below)
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
#   scripts/teardown.sh              # destroys both dev and prod (prompts for each)
#   scripts/teardown.sh --dev        # destroys dev only
#   scripts/teardown.sh --prod       # destroys prod only
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

# ─── Argument parsing ─────────────────────────────────────────────────────────

DESTROY_DEV=false
DESTROY_PROD=false

if [ $# -eq 0 ]; then
  DESTROY_DEV=true
  DESTROY_PROD=true
else
  for arg in "$@"; do
    case $arg in
      --dev)  DESTROY_DEV=true ;;
      --prod) DESTROY_PROD=true ;;
      *)
        echo "Unknown argument: $arg"
        echo "Usage: $0 [--dev] [--prod]   (default: both)"
        exit 1
        ;;
    esac
  done
fi

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
  echo "    • All Azure Container Apps (9 services including PostgreSQL)"
  echo "    • All PostgreSQL data (Azure Files volume)"
  echo "    • Blob Storage account and all data (videos, keyframes, outputs)"
  echo "    • Service Bus namespace and all 5 queues"
  echo "    • Azure Container Registry and all Docker images"
  echo "    • Application Insights + Log Analytics workspace"
  echo "    • Azure Front Door CDN profile"
  echo "    • Azure Communication Services"
  echo "    • Key Vault (see soft-delete note below)"
  echo "    • Resource group $rg"
  echo ""

  if [ "$env" = "prod" ]; then
    echo "  ┌─ PRODUCTION WARNING ─────────────────────────────────────────────┐"
    echo "  │                                                                    │"
    echo "  │  This will permanently delete all production data including       │"
    echo "  │  PostgreSQL data, all uploaded videos, and all output files.      │"
    echo "  │  There is no undo.                                                 │"
    echo "  │                                                                    │"
    echo "  └────────────────────────────────────────────────────────────────────┘"
    echo ""
  fi

  read -r -p "  Type 'destroy-$env' to confirm: " confirm
  if [ "$confirm" != "destroy-$env" ]; then
    echo "  Aborted."
    exit 1
  fi

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

$DESTROY_DEV  && destroy_env dev
$DESTROY_PROD && destroy_env prod

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
