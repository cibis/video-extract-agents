variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type        = string
  description = "Short environment name — used in the ACR name (alphanumeric only, ≤ 50 chars total)"
}

variable "sku" {
  type        = string
  description = "ACR SKU: Basic (dev/test) or Standard (prod)"
  default     = "Basic"
}

variable "tags" {
  type    = map(string)
  default = {}
}
