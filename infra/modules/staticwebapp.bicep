@description('Azure region. SWA Free tier is global but the resource needs a home region.')
param location string = 'eastus2'

@description('SWA resource name')
param staticWebAppName string = 'swa-pfauto'

@description('App Insights connection string (shared with func-pfauto)')
param appInsightsConnectionString string

@description('Storage account name (for KV-reference settings)')
param storageAccountName string

@description('Key Vault name (for KV-reference settings)')
param keyVaultName string

@description('Entra ID tenant ID for SWA Easy Auth')
param tenantId string

// SWA Free — managed Functions in /api are included.
// Note: linked-backend Function Apps require Standard tier; we use managed API instead.
resource swa 'Microsoft.Web/staticSites@2024-04-01' = {
  name: staticWebAppName
  location: location
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    // No repo wired here — deployment is push-based from GitHub Actions.
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
    provider: 'GitHub'
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// App settings — exposed to managed API as env vars
resource swaSettings 'Microsoft.Web/staticSites/config@2024-04-01' = {
  parent: swa
  name: 'appsettings'
  properties: {
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    STORAGE_ACCOUNT_NAME: storageAccountName
    KEY_VAULT_NAME: keyVaultName
    FUNCTION_APP_NAME: 'func-pfauto'
    AZURE_TENANT_ID: tenantId
    // The Entra ID app registration values must be set after the SWA exists
    // (the registration's redirect URI is the SWA hostname). After registering
    // the app and adding a client secret, set:
    //   AAD_CLIENT_ID, AAD_CLIENT_SECRET
    // either via portal or:
    //   az staticwebapp appsettings set --name swa-pfauto --setting-names AAD_CLIENT_ID=<id> AAD_CLIENT_SECRET=<secret>
  }
}

output staticWebAppName string = swa.name
output defaultHostname string = swa.properties.defaultHostname
output principalId string = swa.identity.principalId
