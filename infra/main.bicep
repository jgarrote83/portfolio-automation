@description('Azure region for all resources')
param location string = 'eastus'

@description('Environment tag appended to resource names')
param environment string = 'prod'

@description('Region for the Static Web App (Free SKU is global; metadata sits here). Use eastus2 since SWA Free is not in all regions.')
param swaLocation string = 'eastus2'

// ── Resource names (match CLAUDE.md naming conventions) ─────────────────────────────
var storageAccountName = 'stpfauto${environment}'
var keyVaultName       = 'kv-pfauto-${environment}'
var functionAppName    = 'func-pfauto'
var appServicePlanName = 'plan-pfauto-${environment}'
var logAnalyticsName   = 'log-pfauto-${environment}'
var appInsightsName    = 'appi-pfauto-${environment}'
var staticWebAppName   = 'swa-pfauto'

// ── Monitoring (Log Analytics + App Insights) ────────────────────────────────
module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: {
    location: location
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
  }
}

// ── Storage (Blob containers + Table Storage host) ───────────────────────────
module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    storageAccountName: storageAccountName
  }
}

// ── Key Vault ────────────────────────────────────────────────────────────────
module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    keyVaultName: keyVaultName
  }
}

// ── Function App (Flex Consumption, Python 3.11, Linux) ───────────────────
module functionapp 'modules/functionapp.bicep' = {
  name: 'functionapp'
  params: {
    location: location
    functionAppName: functionAppName
    appServicePlanName: appServicePlanName
    storageAccountName: storageAccountName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    keyVaultUri: keyvault.outputs.keyVaultUri
  }
}

// ── Role assignments: Function App MI → Storage ──────────────────────────────
module storageRoles 'modules/storage-roles.bicep' = {
  name: 'storageRoles'
  params: {
    storageAccountName: storageAccountName
    principalId: functionapp.outputs.principalId
  }
  dependsOn: [storage]
}

// ── Role assignments: Function App MI → Key Vault ────────────────────────────
module kvRoles 'modules/keyvault-roles.bicep' = {
  name: 'kvRoles'
  params: {
    keyVaultName: keyVaultName
    principalId: functionapp.outputs.principalId
  }
}

// ── Event Grid: daily-snapshots BlobCreated → analyzer ──────────────────────
// Flex Consumption requires EventGrid-sourced blob triggers. The analyzer
// function (function_app.py) uses @app.blob_trigger(source="EventGrid").
module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  params: {
    location: location
    storageAccountName: storageAccountName
    functionAppName: functionapp.outputs.functionAppName
  }
  dependsOn: [storage]
}


// ── Static Web App (single pane of glass: report + trade approval) ────────
// Free tier: no managed identity (SWA Free rejects MI assignment). Secrets
// (STORAGE_CONNECTION_STRING, FUNC_MASTER_KEY, AAD_CLIENT_ID/SECRET) are
// set as plain app settings via az CLI post-deploy.
module swa 'modules/staticwebapp.bicep' = {
  name: 'swa'
  params: {
    location: swaLocation
    staticWebAppName: staticWebAppName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    storageAccountName: storageAccountName
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output functionAppName string = functionapp.outputs.functionAppName
output storageAccountName string = storageAccountName
output keyVaultUri string = keyvault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output staticWebAppHostname string = swa.outputs.defaultHostname
