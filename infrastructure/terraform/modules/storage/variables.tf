variable "resource_group_name" {
  type        = string
  description = "Name of the resource group"
}

variable "location" {
  type        = string
  description = "Azure region"
}

variable "environment" {
  type        = string
  description = "Environment name (dev, prod, test)"
}

variable "account_tier" {
  type        = string
  description = "Storage account tier"
  default     = "Standard"
}

variable "replication_type" {
  type        = string
  description = "Storage replication type"
  default     = "LRS"
}

variable "tags" {
  type    = map(string)
  default = {}
}
