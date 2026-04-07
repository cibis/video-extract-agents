variable "pipeline_id" {
  type        = string
  description = "GitLab CI pipeline ID — used to create uniquely named ephemeral resources"
}

variable "location" {
  type    = string
  default = "eastus"
}

variable "image_tag" {
  type = string
}

variable "db_admin_password" {
  type      = string
  sensitive = true
}

variable "agent_model" {
  type    = string
  default = "anthropic/claude-sonnet-4-6"
}

variable "tool_frontier_model" {
  type    = string
  default = "anthropic/claude-opus-4-6"
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
