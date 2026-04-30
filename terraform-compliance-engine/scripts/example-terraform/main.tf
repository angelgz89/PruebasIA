resource "azurerm_resource_group" "example" {
  name     = var.resource_group_name
  location = var.location
}

# ⚠️ Storage account con varios fallos intencionales para que los próximos
# steps del compliance engine los detecten:
#   - https_traffic_only desactivado
#   - min_tls_version por debajo de TLS1_2
#   - public_network_access_enabled = true
#   - sin network_rules
#   - sin tags obligatorios
resource "azurerm_storage_account" "example" {
  name                            = var.storage_account_name
  resource_group_name             = azurerm_resource_group.example.name
  location                        = azurerm_resource_group.example.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  enable_https_traffic_only       = false
  min_tls_version                 = "TLS1_0"
  public_network_access_enabled   = true
  allow_nested_items_to_be_public = true

  blob_properties {
    versioning_enabled       = false
    change_feed_enabled      = false
    last_access_time_enabled = false
  }
}

# ⚠️ Container con acceso público a nivel blob.
resource "azurerm_storage_container" "example_public" {
  name                  = "public-data"
  storage_account_name  = azurerm_storage_account.example.name
  container_access_type = "blob"
}

resource "azurerm_storage_container" "example_private" {
  name                  = "private-data"
  storage_account_name  = azurerm_storage_account.example.name
  container_access_type = "private"
}
