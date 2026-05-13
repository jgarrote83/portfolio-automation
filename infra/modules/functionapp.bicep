param location string
param functionAppName string
param appServicePlanName string
param storageAccountName string
param appInsightsConnectionString string
param keyVaultUri string

// Shared key needed only for WEBSITE_CONTENTAZUREFILECONNECTIONSTRING (Azure Files host share).
// All application data access uses managed identity via AzureWebJobsStorage__ identity-based connection.
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

var storageKey = storageAccount.listKeys().keys[0].value
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storageAccountName};AccountKey=${storageKey};EndpointSuffix=core.windows.net'

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: appServicePlanName
  location: location
  kind: 'linux'
  sku: {
    name: 'Y1'      // Consumption — pay per execution, ~$0 at 22 runs/month
    tier: 'Dynamic'
  }
  properties: {
    reserved: true  // required for Linux
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'  // managed identity — no credentials in code
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_EXTENSION_VERSION',              value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME',                  value: 'python' }
        { name: 'PYTHON_ISOLATE_WORKER_DEPENDENCIES',        value: '1' }
        { name: 'AzureWebJobsStorage__accountName',          value: storageAccountName }
        { name: 'AzureWebJobsStorage__credential',           value: 'managedidentity' }
        { name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING',  value: storageConnectionString }
        { name: 'WEBSITE_CONTENTSHARE',                      value: toLower(functionAppName) }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING',     value: appInsightsConnectionString }
        { name: 'KEY_VAULT_URI',                             value: keyVaultUri }
        { name: 'STORAGE_ACCOUNT_NAME',                      value: storageAccountName }
        { name: 'WEBSITE_AUTH_MI_ENABLED',                   value: 'TRUE' }
        // WEBSITE_RUN_FROM_PACKAGE is set by the deploy-code.yml workflow after first deploy
      ]
    }
  }
}

output principalId string = functionApp.identity.principalId
output functionAppName string = functionApp.name
