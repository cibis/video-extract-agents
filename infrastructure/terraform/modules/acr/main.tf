resource "azurerm_container_registry" "main" {
  name                = "videoextract${var.environment}acr"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku
  admin_enabled       = true
  tags                = var.tags
}
