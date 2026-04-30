variable "location" {
  type        = string
  description = "Azure region donde se despliegan los recursos."
  default     = "westeurope"
}

variable "resource_group_name" {
  type        = string
  description = "Nombre del resource group."
  default     = "rg-pruebas-storage"
}

variable "storage_account_name" {
  type        = string
  description = "Nombre del storage account (3-24 chars, lowercase, sin guiones)."
  default     = "stpruebasiademo01"
}

# ⚠️ Variable con default sensible hardcodeado — fallo intencional para tests futuros.
variable "shared_access_key" {
  type        = string
  description = "Clave compartida de acceso (NO debería tener default real)."
  default     = "ExamplePlaintextKey-DO-NOT-USE-IN-PROD"
  sensitive   = true
}
