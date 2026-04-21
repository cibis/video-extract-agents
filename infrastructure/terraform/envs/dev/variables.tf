variable "subscription_id" {
  type        = string
  description = "Azure subscription ID — required by azurerm provider 4.x when use_cli = false"
}

variable "location" {
  type    = string
  default = "eastus"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "db_admin_password" {
  type      = string
  sensitive = true
}

variable "agent_model" {
  type    = string
  default = "bedrock/us.amazon.nova-2-lite-v1:0"
}

variable "tool_frontier_model" {
  type    = string
  default = "bedrock/us.amazon.nova-2-lite-v1:0"
}

variable "model_aliases_override" {
  type    = string
  default = ""
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

# NOTE: Entra External ID is not provisioned by Terraform (tenant-level resource).
# Set via TF_VAR_entra_tenant_id and TF_VAR_entra_client_id in CI.
variable "entra_tenant_id" {
  type    = string
  default = ""
}

variable "entra_client_id" {
  type    = string
  default = ""
}

variable "app_base_url" {
  type    = string
  default = ""
}

# LibreChat secrets — inject via TF_VAR_* in CI (GitLab CI/CD variables)
variable "librechat_creds_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "librechat_creds_iv" {
  type      = string
  sensitive = true
  default   = ""
}

variable "librechat_jwt_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "librechat_jwt_refresh_secret" {
  type      = string
  sensitive = true
  default   = ""
}

variable "librechat_secret_key" {
  type      = string
  sensitive = true
  default   = ""
}

variable "librechat_agent_api_key" {
  type    = string
  default = "dev-key"
}
