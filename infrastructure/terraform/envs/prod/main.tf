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
  features {
    key_vault {
      purge_soft_delete_on_destroy    = true
      recover_soft_deleted_key_vaults = true
    }
  }
  use_cli         = false
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "main" {
  name     = "video-extract-prod"
  location = var.location
  tags     = local.tags
}

locals {
  environment = "prod"
  tags = {
    environment = "prod"
    project     = "video-extract"
    managed-by  = "terraform"
  }
}

module "storage" {
  source              = "../../modules/storage"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  environment         = local.environment
  replication_type    = "ZRS"
  tags                = local.tags
}


resource "azurerm_servicebus_namespace" "main" {
  name                = "videoextract-prod-servicebus"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  sku                 = "Premium"
  capacity            = 1
  tags                = local.tags
}

resource "azurerm_servicebus_queue" "queues" {
  for_each           = toset(["video-uploaded", "video-indexed", "job-queued"])
  name               = each.key
  namespace_id       = azurerm_servicebus_namespace.main.id
  max_delivery_count = 10
}

module "acr" {
  source              = "../../modules/acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = var.location
  environment         = local.environment
  sku                 = "Standard"
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
  db_storage_gb                 = 128
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
  appinsights_connection_string = module.appinsights.connection_string
  front_door_url                = module.frontdoor.endpoint_hostname
  entra_tenant_id               = var.entra_tenant_id
  entra_client_id               = var.entra_client_id
  app_base_url                  = var.app_base_url
  min_replicas                  = 1
  max_replicas                  = 20
  postgres_persistent_volume    = false
  create_db_init_job            = true
  tags                          = local.tags
}

module "appinsights" {
  source                     = "../../modules/appinsights"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = var.location
  environment                = local.environment
  log_analytics_workspace_id = module.aca.log_analytics_workspace_id
  tags                       = local.tags
}

module "frontdoor" {
  source              = "../../modules/frontdoor"
  resource_group_name = azurerm_resource_group.main.name
  environment         = local.environment
  api_gateway_fqdn    = module.aca.api_gateway_fqdn
  tags                = local.tags
}

# NOTE: Entra External ID is not provisioned by Terraform — it is a tenant-level resource
# provisioned once manually (see SETUP.md §6). Tenant ID and client ID are injected as
# variables and threaded through to the api-gateway container.

module "keyvault" {
  source                        = "../../modules/keyvault"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = var.location
  environment                   = local.environment
  anthropic_api_key             = var.anthropic_api_key
  db_admin_password             = var.db_admin_password
  storage_connection_string     = module.storage.primary_connection_string
  service_bus_connection_string = azurerm_servicebus_namespace.main.default_primary_connection_string
  appinsights_connection_string = module.appinsights.connection_string
  tags                          = local.tags
}
