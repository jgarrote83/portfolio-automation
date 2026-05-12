param location string
param keyVaultName string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'           // standard tier — no HSM, ~$0 at our call volume
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true  // RBAC only, no legacy access policies
    enableSoftDelete: true
    softDeleteRetentionInDays: 7   // minimum allowed
    publicNetworkAccess: 'Enabled'
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
