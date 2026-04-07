output "server_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "server_name" {
  value = azurerm_postgresql_flexible_server.main.name
}

output "database_name" {
  value = azurerm_postgresql_flexible_server_database.app.name
}

output "connection_string" {
  value     = "postgresql+asyncpg://${var.admin_username}:${var.admin_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/videoextract"
  sensitive = true
}
