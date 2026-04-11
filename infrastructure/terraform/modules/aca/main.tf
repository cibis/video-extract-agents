locals {
  database_url_asyncpg = "postgresql+asyncpg://${var.db_admin_username}:${var.db_admin_password}@postgresql:5432/videoextract"
  database_url_pg      = "postgresql://${var.db_admin_username}:${var.db_admin_password}@postgresql:5432/videoextract"
}

# ─── PostgreSQL (container) ───────────────────────────────────────────────────

resource "azurerm_storage_share" "postgres_data" {
  name               = "postgres-data"
  storage_account_id = var.storage_account_id
  quota              = var.db_storage_gb
}

resource "azurerm_container_app_environment_storage" "postgres_data" {
  name                         = "postgres-data"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = var.storage_account_name
  share_name                   = azurerm_storage_share.postgres_data.name
  access_key                   = var.storage_account_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app" "postgresql" {
  name                         = "postgresql"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  template {
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "postgresql"
      image  = "postgres:15-alpine"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "POSTGRES_USER"
        value = var.db_admin_username
      }
      env {
        name  = "POSTGRES_DB"
        value = "videoextract"
      }
      env {
        name        = "POSTGRES_PASSWORD"
        secret_name = "db-admin-password"
      }
      # PGDATA must be a subdirectory of the Azure Files mount — Azure Files
      # creates a lost+found dir at the root which prevents postgres startup.
      env {
        name  = "PGDATA"
        value = "/var/lib/postgresql/data/pgdata"
      }

      volume_mounts {
        name = "postgres-data"
        path = "/var/lib/postgresql/data"
      }

      liveness_probe {
        transport = "TCP"
        port      = 5432
      }
      readiness_probe {
        transport = "TCP"
        port      = 5432
      }
    }

    volume {
      name         = "postgres-data"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.postgres_data.name
    }
  }

  secret {
    name  = "db-admin-password"
    value = var.db_admin_password
  }

  ingress {
    external_enabled = false
    target_port      = 5432
    transport        = "tcp"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  depends_on = [azurerm_container_app_environment_storage.postgres_data]
}

# ─────────────────────────────────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "main" {
  name                = "videoextract-${var.environment}-law"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_container_app_environment" "main" {
  name                       = "videoextract-${var.environment}-cae"
  resource_group_name        = var.resource_group_name
  location                   = var.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = var.tags
}

# ─── API Gateway ─────────────────────────────────────────────────────────────

resource "azurerm_container_app" "api_gateway" {
  name                         = "api-gateway"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "api-gateway"
      image  = "${var.acr_login_server}/api-gateway:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "DATABASE_URL"
        value = local.database_url_pg
      }
      env {
        name  = "AZURE_STORAGE_CONNECTION_STRING"
        value = var.storage_connection_string
      }
      env {
        name  = "AZURE_SERVICE_BUS_CONNECTION_STRING"
        value = var.service_bus_connection_string
      }
      env {
        name  = "AGENT_ORCHESTRATOR_URL"
        value = "http://agent-orchestrator"
      }
      env {
        name  = "OUTPUT_URL_MODE"
        value = "frontdoor"
      }
      env {
        name  = "FRONT_DOOR_ENDPOINT"
        value = var.front_door_url
      }
      env {
        name  = "AZURE_ENTRA_TENANT_ID"
        value = var.entra_tenant_id
      }
      env {
        name  = "AZURE_ENTRA_CLIENT_ID"
        value = var.entra_client_id
      }
      env {
        name  = "AZURE_ENTRA_JWKS_URI"
        value = var.entra_tenant_id != "" ? "https://login.microsoftonline.com/${var.entra_tenant_id}/discovery/v2.0/keys" : ""
      }
      env {
        name  = "LOCAL_DEV_SKIP_AUTH"
        value = var.local_dev_skip_auth ? "true" : ""
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    http_scale_rule {
      name                = "http-scaling"
      concurrent_requests = "50"
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  depends_on = [azurerm_container_app.postgresql]
}

# ─── Agent Orchestrator ───────────────────────────────────────────────────────

resource "azurerm_container_app" "agent_orchestrator" {
  name                         = "agent-orchestrator"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "agent-orchestrator"
      image  = "${var.acr_login_server}/agent-orchestrator:${var.image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "DATABASE_URL"
        value = local.database_url_asyncpg
      }
      env {
        name  = "AZURE_STORAGE_CONNECTION_STRING"
        value = var.storage_connection_string
      }
      env {
        name  = "AZURE_SERVICE_BUS_CONNECTION_STRING"
        value = var.service_bus_connection_string
      }
      env {
        name  = "AGENT_MODEL"
        value = var.agent_model
      }
      env {
        name  = "ANTHROPIC_API_KEY"
        value = var.anthropic_api_key
      }
      env {
        name  = "OPENAI_API_KEY"
        value = var.openai_api_key
      }
      env {
        name  = "AWS_ACCESS_KEY_ID"
        value = var.aws_access_key_id
      }
      env {
        name  = "AWS_SECRET_ACCESS_KEY"
        value = var.aws_secret_access_key
      }
      env {
        name  = "AWS_REGION_NAME"
        value = var.aws_region_name
      }
      env {
        name  = "MCP_ANALYSIS_URL"
        value = "http://mcp-server-analysis"
      }
      env {
        name  = "MCP_PROCESSING_URL"
        value = "http://mcp-server-processing"
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    custom_scale_rule {
      name             = "servicebus-scale"
      custom_rule_type = "azure-servicebus"
      metadata = {
        namespace    = var.service_bus_namespace
        queueName    = "job-queued"
        messageCount = "5"
      }
    }
  }

  ingress {
    external_enabled = false
    target_port      = 8001
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  depends_on = [azurerm_container_app.postgresql]
}

# ─── MCP Server Analysis ──────────────────────────────────────────────────────

resource "azurerm_container_app" "mcp_server_analysis" {
  name                         = "mcp-server-analysis"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "mcp-server-analysis"
      image  = "${var.acr_login_server}/mcp-server-analysis:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "AZURE_STORAGE_CONNECTION_STRING"
        value = var.storage_connection_string
      }
      env {
        name  = "TOOL_FRONTIER_MODEL"
        value = var.tool_frontier_model
      }
      env {
        name  = "MODEL_ALIASES_OVERRIDE"
        value = var.model_aliases_override
      }
      env {
        name  = "ANTHROPIC_API_KEY"
        value = var.anthropic_api_key
      }
      env {
        name  = "OPENAI_API_KEY"
        value = var.openai_api_key
      }
      env {
        name  = "AWS_ACCESS_KEY_ID"
        value = var.aws_access_key_id
      }
      env {
        name  = "AWS_SECRET_ACCESS_KEY"
        value = var.aws_secret_access_key
      }
      env {
        name  = "AWS_REGION_NAME"
        value = var.aws_region_name
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    http_scale_rule {
      name                = "http-scaling"
      concurrent_requests = "20"
    }
  }

  ingress {
    external_enabled = false
    target_port      = 8100
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

# ─── MCP Server Processing ────────────────────────────────────────────────────

resource "azurerm_container_app" "mcp_server_processing" {
  name                         = "mcp-server-processing"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "mcp-server-processing"
      image  = "${var.acr_login_server}/mcp-server-processing:${var.image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "AZURE_STORAGE_CONNECTION_STRING"
        value = var.storage_connection_string
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    custom_scale_rule {
      name             = "servicebus-scale"
      custom_rule_type = "azure-servicebus"
      metadata = {
        namespace    = var.service_bus_namespace
        queueName    = "job-queued"
        messageCount = "5"
      }
    }
  }

  ingress {
    external_enabled = false
    target_port      = 8200
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

# ─── Preprocessing Worker ─────────────────────────────────────────────────────

resource "azurerm_container_app" "preprocessing_worker" {
  name                         = "preprocessing-worker"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "preprocessing-worker"
      image  = "${var.acr_login_server}/preprocessing-worker:${var.image_tag}"
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "DATABASE_URL"
        value = local.database_url_asyncpg
      }
      env {
        name  = "AZURE_STORAGE_CONNECTION_STRING"
        value = var.storage_connection_string
      }
      env {
        name  = "AZURE_SERVICE_BUS_CONNECTION_STRING"
        value = var.service_bus_connection_string
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    custom_scale_rule {
      name             = "servicebus-scale"
      custom_rule_type = "azure-servicebus"
      metadata = {
        namespace    = var.service_bus_namespace
        queueName    = "video-uploaded"
        messageCount = "5"
      }
    }
  }

  depends_on = [azurerm_container_app.postgresql]
}

# ─── Notification Worker ──────────────────────────────────────────────────────

resource "azurerm_container_app" "notification_worker" {
  name                         = "notification-worker"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "notification-worker"
      image  = "${var.acr_login_server}/notification-worker:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "DATABASE_URL"
        value = local.database_url_asyncpg
      }
      env {
        name  = "AZURE_SERVICE_BUS_CONNECTION_STRING"
        value = var.service_bus_connection_string
      }
      env {
        name  = "NOTIFICATION_MODE"
        value = "acs"
      }
      env {
        name  = "AZURE_COMMUNICATION_SERVICES_CONNECTION_STRING"
        value = var.acs_connection_string
      }
      env {
        name  = "FRONT_DOOR_HOSTNAME"
        value = var.front_door_url
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    custom_scale_rule {
      name             = "servicebus-scale"
      custom_rule_type = "azure-servicebus"
      metadata = {
        namespace    = var.service_bus_namespace
        queueName    = "job-completed"
        messageCount = "5"
      }
    }
  }

  depends_on = [azurerm_container_app.postgresql]
}

# ─── Angular Frontend ─────────────────────────────────────────────────────────

resource "azurerm_container_app" "angular_shell" {
  name                         = "angular-shell"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "angular-shell"
      image  = "${var.acr_login_server}/angular-shell:${var.image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "API_GATEWAY_URL"
        value = "http://api-gateway"
      }
      env {
        name  = "LIBRECHAT_URL"
        value = "http://librechat"
      }
    }

    http_scale_rule {
      name                = "http-scaling"
      concurrent_requests = "50"
    }
  }

  ingress {
    external_enabled = true
    target_port      = 80
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}

# ─── LibreChat ────────────────────────────────────────────────────────────────

resource "azurerm_container_app" "librechat" {
  name                         = "librechat"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"
  tags                         = var.tags

  registry {
    server               = var.acr_login_server
    username             = var.acr_username
    password_secret_name = "acr-password"
  }

  secret {
    name  = "acr-password"
    value = var.acr_password
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "librechat"
      image  = "${var.acr_login_server}/librechat:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "API_GATEWAY_URL"
        value = "http://api-gateway"
      }
      env {
        name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
        value = var.appinsights_connection_string
      }
    }

    http_scale_rule {
      name                = "http-scaling"
      concurrent_requests = "50"
    }
  }

  ingress {
    # LibreChat is embedded via iframe — browser must reach it directly
    external_enabled = true
    target_port      = 3080
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }
}
