@description('Azure region for all resources')
param location string = 'eastus'

@description('Environment tag appended to resource names')
param environment string = 'prod'

@description('Entra ID tenant for SWA Easy Auth (work/school login)')
param tenantId string = subscription().tenantId

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


// ── Static Web App (single pane of glass: report + trade approval) ────────
module swa 'modules/staticwebapp.bicep' = {
  name: 'swa'
  params: {
    location: swaLocation
    staticWebAppName: staticWebAppName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    storageAccountName: storageAccountName
    keyVaultName: keyVaultName
    tenantId: tenantId
  }
}

// ── Role assignments: SWA MI → Storage + Key Vault ────────────────────────
module swaRoles 'modules/staticwebapp-roles.bicep' = {
  name: 'swaRoles'
  params: {
    storageAccountName: storageAccountName
    keyVaultName: keyVaultName
    principalId: swa.outputs.principalId
  }
  dependsOn: [storage, keyvault]
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output functionAppName string = functionapp.outputs.functionAppName
output storageAccountName string = storageAccountName
output keyVaultUri string = keyvault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output staticWebAppHostname string = swa.outputs.defaultHostname
