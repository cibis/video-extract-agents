resource "azurerm_communication_service" "main" {
  name                = "videoextract-${var.environment}-acs"
  resource_group_name = var.resource_group_name
  data_location       = "United States"
  tags                = var.tags
}
