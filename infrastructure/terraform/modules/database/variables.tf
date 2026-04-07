variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "environment" {
  type = string
}

variable "sku_name" {
  type        = string
  description = "PostgreSQL SKU"
  default     = "Standard_B1ms"
}

variable "storage_mb" {
  type    = number
  default = 32768
}

variable "admin_username" {
  type    = string
  default = "psqladmin"
}

variable "admin_password" {
  type      = string
  sensitive = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
