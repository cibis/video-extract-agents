output "api_gateway_fqdn" {
  value = azurerm_container_app.api_gateway.latest_revision_fqdn
}

output "agent_orchestrator_fqdn" {
  value = azurerm_container_app.agent_orchestrator.latest_revision_fqdn
}

output "mcp_analysis_fqdn" {
  value = azurerm_container_app.mcp_server_analysis.latest_revision_fqdn
}

output "mcp_processing_fqdn" {
  value = azurerm_container_app.mcp_server_processing.latest_revision_fqdn
}

output "aca_environment_id" {
  value = azurerm_container_app_environment.main.id
}

output "log_analytics_workspace_id" {
  value = azurerm_log_analytics_workspace.main.id
}

output "angular_frontend_fqdn" {
  value = azurerm_container_app.angular_frontend.latest_revision_fqdn
}

output "librechat_fqdn" {
  value = azurerm_container_app.librechat.latest_revision_fqdn
}
