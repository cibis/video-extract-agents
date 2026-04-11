terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {}
  use_cli = false
}

# Ephemeral test environment — destroyed after every CI pipeline run
resource "azurerm_resource_group" "main" {
  name     = "video-extract-test-${var.pipeline_id}"
  location = var.location
  tags     = local.tags
}

locals {
  environment = "test-${var.pipeline_id}"
  tags = {
    environment = "test"
    pipeline-id = var.pipeline_id
    project     = "video-extract"
    managed-by  = "ci"
    ttl         = "2h"
  }
}

module "storage" {
  source              = "../../modules/storage"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  environment         = "test${var.pipeline_id}"
  tags                = local.tags
}


resource "azurerm_servicebus_namespace" "main" {
  name                = "ve-test-${var.pipeline_id}-sb"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_servicebus_queue" "queues" {
  for_each           = toset(["video-uploaded", "video-indexed", "job-queued", "job-completed", "job-failed"])
  name               = each.key
  namespace_id       = azurerm_servicebus_namespace.main.id
  max_delivery_count = 5
}

module "acr" {
  source              = "../../modules/acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  # Use pipeline_id suffix to avoid ACR name collisions between concurrent test runs
  environment         = "test${var.pipeline_id}"
  sku                 = "Basic"
  tags                = local.tags
}

module "appcommunication" {
  source              = "../../modules/appcommunication"
  resource_group_name = azurerm_resource_group.main.name
  environment         = local.environment
  tags                = local.tags
}

module "aca" {
  source                        = "../../modules/aca"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = var.location
  environment                   = local.environment
  acr_login_server              = module.acr.login_server
  acr_username                  = module.acr.admin_username
  acr_password                  = module.acr.admin_password
  image_tag                     = var.image_tag
  service_bus_namespace         = azurerm_servicebus_namespace.main.name
  service_bus_connection_string = azurerm_servicebus_namespace.main.default_primary_connection_string
  storage_connection_string     = module.storage.primary_connection_string
  db_admin_password             = var.db_admin_password
  storage_account_id            = module.storage.storage_account_id
  storage_account_name          = module.storage.storage_account_name
  storage_account_key           = module.storage.primary_access_key
  agent_model                   = var.agent_model
  tool_frontier_model           = var.tool_frontier_model
  model_aliases_override        = var.model_aliases_override
  anthropic_api_key             = var.anthropic_api_key
  openai_api_key                = var.openai_api_key
  aws_access_key_id             = var.aws_access_key_id
  aws_secret_access_key         = var.aws_secret_access_key
  aws_region_name               = var.aws_region_name
  appinsights_connection_string = ""
  acs_connection_string         = module.appcommunication.primary_connection_string
  front_door_url                = ""
  min_replicas                  = 0
  max_replicas                  = 3
  tags                          = local.tags
}

# App Insights, Front Door, and Key Vault are not provisioned for ephemeral test environments:
# - App Insights: skipped (appinsights_connection_string = "" above)
# - Front Door: too slow to provision per pipeline run; api-gateway OUTPUT_URL_MODE=frontdoor
#   remains set but FRONT_DOOR_URL is empty — E2E tests should not rely on signed CDN URLs
# - Key Vault: test env uses direct env vars injected via TF_VAR_* in CI
