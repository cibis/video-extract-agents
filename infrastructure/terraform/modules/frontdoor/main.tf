resource "azurerm_cdn_frontdoor_profile" "main" {
  name                = "videoextract-${var.environment}-fd"
  resource_group_name = var.resource_group_name
  sku_name            = "Standard_AzureFrontDoor"
  tags                = var.tags
}

resource "azurerm_cdn_frontdoor_endpoint" "main" {
  name                     = "videoextract-${var.environment}"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id
  tags                     = var.tags
}

resource "azurerm_cdn_frontdoor_origin_group" "main" {
  name                     = "api-gateway-og"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.main.id

  load_balancing {}
}

resource "azurerm_cdn_frontdoor_origin" "api_gateway" {
  name                          = "api-gateway"
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.main.id
  enabled                       = true
  certificate_name_check_enabled = true

  host_name          = var.api_gateway_fqdn
  origin_host_header = var.api_gateway_fqdn
  https_port         = 443
  http_port          = 80
  priority           = 1
  weight             = 1000
}

resource "azurerm_cdn_frontdoor_route" "main" {
  name                          = "default-route"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.main.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.main.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.api_gateway.id]
  supported_protocols           = ["Http", "Https"]
  patterns_to_match             = ["/*"]
  forwarding_protocol           = "HttpsOnly"
  https_redirect_enabled        = true
  link_to_default_domain        = true
}
