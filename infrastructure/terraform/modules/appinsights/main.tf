resource "azurerm_application_insights" "main" {
  name                = "videoextract-${var.environment}-ai"
  resource_group_name = var.resource_group_name
  location            = var.location
  workspace_id        = var.log_analytics_workspace_id
  application_type    = "web"
  tags                = var.tags
}
