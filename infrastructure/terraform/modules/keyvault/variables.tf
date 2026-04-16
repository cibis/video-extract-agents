variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type        = string
  description = "Short environment name — used in Key Vault name (3–24 chars total: ve-<env>-kv)"
}

variable "anthropic_api_key" {
  type      = string
  sensitive = true
}

variable "db_admin_password" {
  type      = string
  sensitive = true
}

variable "storage_connection_string" {
  type      = string
  sensitive = true
}

variable "service_bus_connection_string" {
  type      = string
  sensitive = true
}

variable "acs_connection_string" {
  type      = string
  sensitive = true
}

variable "appinsights_connection_string" {
  type        = string
  sensitive   = true
  default     = ""
  description = "App Insights connection string — empty string skips secret creation"
}

variable "tags" {
  type    = map(string)
  default = {}
}
