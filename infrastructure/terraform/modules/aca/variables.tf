variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type = string
}

variable "acr_login_server" {
  type = string
}

variable "acr_username" {
  type      = string
  sensitive = true
}

variable "acr_password" {
  type      = string
  sensitive = true
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "service_bus_namespace" {
  type = string
}

variable "service_bus_connection_string" {
  type      = string
  sensitive = true
}

variable "storage_connection_string" {
  type      = string
  sensitive = true
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "agent_model" {
  type        = string
  description = "LiteLLM model string for agent reasoning, e.g. anthropic/claude-sonnet-4-6, openai/gpt-4o, bedrock/us.anthropic.claude-sonnet-4-5-20251001-v1:0"
  default     = "anthropic/claude-sonnet-4-6"
}

variable "tool_frontier_model" {
  type        = string
  description = "LiteLLM model string for mcp-server-analysis vision tools"
  default     = "anthropic/claude-opus-4-6"
}

variable "model_aliases_override" {
  type        = string
  description = "Comma-separated alias=model pairs to override defaults in mcp-server-analysis"
  default     = ""
}

variable "anthropic_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "openai_api_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "aws_access_key_id" {
  type      = string
  sensitive = true
  default   = ""
}

variable "aws_secret_access_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "aws_region_name" {
  type    = string
  default = "us-east-1"
}

variable "appinsights_connection_string" {
  type      = string
  sensitive = true
  default   = ""
}

variable "acs_connection_string" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Azure Communication Services connection string for notification-worker"
}

variable "front_door_url" {
  type        = string
  default     = ""
  description = "Azure Front Door endpoint hostname; injected as FRONT_DOOR_URL (api-gateway) and FRONT_DOOR_HOSTNAME (notification-worker)"
}

variable "entra_tenant_id" {
  type        = string
  default     = ""
  description = "Azure Entra External ID tenant ID for JWT validation in api-gateway"
}

variable "entra_client_id" {
  type        = string
  default     = ""
  description = "Azure Entra External ID client/app ID for JWT validation in api-gateway"
}

variable "min_replicas" {
  type    = number
  default = 0
}

variable "max_replicas" {
  type    = number
  default = 10
}

variable "tags" {
  type    = map(string)
  default = {}
}
