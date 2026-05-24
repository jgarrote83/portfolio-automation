@description('Storage account that hosts daily-reports / daily-trades / daily-snapshots / daily-executions')
param storageAccountName string

@description('Key Vault that holds API keys (Alpaca, function master key reference)')
param keyVaultName string

@description('SWA system-assigned principal id')
param principalId string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// SWA managed API only reads from storage (lists/downloads reports, trades, snapshots)
// and writes to a single container for approval state — start read-only and extend later.

// Storage Blob Data Reader
resource blobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Table Data Reader — for TradeHistory / PortfolioHistory views
resource tableReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, '76199698-9eea-4c19-bc75-cec21354c6b6')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '76199698-9eea-4c19-bc75-cec21354c6b6')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Storage Blob Data Contributor on the approvals container only — handled
// at app level by writing into a dedicated container `approvals/`. For now we
// grant Contributor at the account scope; can be narrowed later via a dedicated
// container-level role assignment if needed.
resource blobContrib 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, 'ba92f5b4-2d11-453d-a403-e96b0029c9fe', 'swa')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User — read AAD client secret + function master key references
resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, principalId, '4633458b-17de-408a-b874-0445c86b69e6', 'swa')
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
