output "endpoint_hostname" {
  value       = azurerm_cdn_frontdoor_endpoint.main.host_name
  description = "Front Door endpoint hostname — use as FRONT_DOOR_URL in api-gateway and FRONT_DOOR_HOSTNAME in notification-worker"
}

output "profile_id" {
  value = azurerm_cdn_frontdoor_profile.main.id
}
