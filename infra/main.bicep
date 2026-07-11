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
// Only the System Topic is created here. The event subscription is wired up
// by deploy-code.yml AFTER code deploy, once the blobs_extension key exists.
module eventgrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  params: {
    location: location
    storageAccountName: storageAccountName
  }
  dependsOn: [storage]
}


// ── Key Vault reference for deploy-time secret resolution (SWA hardening) ────
// `getSecret()` can only be used in a module's `params` block, against an
// `existing` Microsoft.KeyVault/vaults resource — see modules/keyvault.bicep's
// `enabledForTemplateDeployment` note (FOLLOWUPS #2).
resource keyVaultRef 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// ── Static Web App (single pane of glass: report + trade approval) ────────
// Free tier: no managed identity — Azure Static Web Apps managed functions
// support neither Key Vault app-setting references nor managed identity on
// ANY plan (verified 2026-07-11; see modules/staticwebapp.bicep). Secrets are
// resolved from Key Vault at DEPLOY TIME instead (getSecret() below), so
// `az deployment group create` sets them fresh every time rather than wiping
// them — the fix for FOLLOWUPS #2.
module swa 'modules/staticwebapp.bicep' = {
  name: 'swa'
  params: {
    location: swaLocation
    staticWebAppName: staticWebAppName
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    storageAccountName: storageAccountName
    storageConnectionStringSecret: keyVaultRef.getSecret('swa-storage-connection-string')
    funcMasterKeySecret: keyVaultRef.getSecret('swa-func-master-key')
  }
  dependsOn: [keyvault]
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output functionAppName string = functionapp.outputs.functionAppName
output storageAccountName string = storageAccountName
output keyVaultUri string = keyvault.outputs.keyVaultUri
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output staticWebAppHostname string = swa.outputs.defaultHostname
