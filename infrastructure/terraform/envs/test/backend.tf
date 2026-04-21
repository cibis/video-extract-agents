terraform {
  backend "azurerm" {
    resource_group_name  = "terraform-state-rg"
    storage_account_name = "tfstatevideoextract"
    container_name       = "tfstate"
    key                  = "video-extract/test/terraform.tfstate"
  }
}
