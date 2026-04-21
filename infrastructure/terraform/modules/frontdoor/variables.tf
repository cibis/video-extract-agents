variable "resource_group_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "api_gateway_fqdn" {
  type        = string
  description = "FQDN of the ACA api-gateway container app (from module.aca.api_gateway_fqdn)"
}

variable "tags" {
  type    = map(string)
  default = {}
}
