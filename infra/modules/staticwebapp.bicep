@description('Azure region. SWA Free metadata sits here. Use eastus2 (Free is not in all regions).')
param location string = 'eastus2'

@description('SWA resource name')
param staticWebAppName string = 'swa-pfauto'

@description('App Insights connection string (shared with func-pfauto)')
param appInsightsConnectionString string

@description('Storage account name (exposed to managed API as env var)')
param storageAccountName string

// SWA Free — managed Functions in /api are included.
// No system-assigned identity: SWA Free rejects identity assignment with
// SkuCode 'Free' is invalid (Standard tier required for MI / KV references).
// Secrets are placed as plain app settings post-deploy (single-user system).
// API pinned to 2022-03-01: 2024-04-01 rejects Free with or without explicit sku.
resource swa 'Microsoft.Web/staticSites@2022-03-01' = {
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
}

// App settings — exposed to managed API as env vars.
// Secret values (STORAGE_CONNECTION_STRING, FUNC_MASTER_KEY, AAD_CLIENT_ID,
// AAD_CLIENT_SECRET) are set post-deploy via:
//   az staticwebapp appsettings set --name swa-pfauto --setting-names <k>=<v>
resource swaSettings 'Microsoft.Web/staticSites/config@2022-03-01' = {
  parent: swa
  name: 'appsettings'
  properties: {
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    STORAGE_ACCOUNT_NAME: storageAccountName
    FUNCTION_APP_NAME: 'func-pfauto'
  }
}

output staticWebAppName string = swa.name
output defaultHostname string = swa.properties.defaultHostname
