param location string
param storageAccountName string

var containers = [
  'daily-snapshots'
  'daily-reports'
  'daily-trades'
  'daily-executions'
  'deployment'       // Flex Consumption one-deploy package container
]

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }  // cheapest — locally redundant, fine for single-user
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true     // SWA managed API still uses STORAGE_CONNECTION_STRING; Flex func app uses identity-based auth and does not need this
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in containers: {
  parent: blobService
  name: c
  properties: { publicAccess: 'None' }
}]

output storageAccountId string = storageAccount.id
output storageAccountName string = storageAccount.name
