variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type = string
}

variable "log_analytics_workspace_id" {
  type        = string
  description = "ID of the Log Analytics workspace to link to (from module.aca.log_analytics_workspace_id)"
}

variable "tags" {
  type    = map(string)
  default = {}
}
