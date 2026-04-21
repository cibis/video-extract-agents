output "primary_connection_string" {
  value     = azurerm_communication_service.main.primary_connection_string
  sensitive = true
}

output "acs_id" {
  value = azurerm_communication_service.main.id
}
