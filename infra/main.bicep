@description('Azure region for all resources')
param location string = 'eastus'

@description('Environment tag appended to resource names')
param environment string = 'prod'

// ── Resource names (match CLAUDE.md naming conventions) ──────────────────────
var storageAccountName = 'stpfauto${environment}'
var keyVaultName       = 'kv-pfauto-${environment}'
var functionAppName    = 'func-pfauto'
var appServicePlanName = 'plan-pfauto-${environment}'
var logAnalyticsName   = 'log-pfauto-${environment}'
var appInsightsName    = 'appi-pfauto-${environment}'

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

// ── Function App (Consumption plan, Python 3.11, Linux) ─────────────────────
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


// ── Outputs ──────────────────────────────────────────────────────────────────
output functionAppName string = functionapp.outputs.functionAppName
output storageAccountName string = storageAccountName
output keyVaultUri string = keyvault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
