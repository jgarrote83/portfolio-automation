@description('Azure region. SWA Free metadata sits here. Use eastus2 (Free is not in all regions).')
param location string = 'eastus2'

@description('SWA resource name')
param staticWebAppName string = 'swa-pfauto'

@description('App Insights connection string (shared with func-pfauto)')
param appInsightsConnectionString string

@description('Storage account name (exposed to managed API as env var)')
param storageAccountName string

@description('Storage account connection string, resolved from Key Vault at DEPLOY TIME by main.bicep (keyVault.getSecret(...)) — see the note on swaSettings below.')
@secure()
param storageConnectionStringSecret string

@description('func-pfauto master key, resolved from Key Vault at DEPLOY TIME by main.bicep — same rationale as storageConnectionStringSecret.')
@secure()
param funcMasterKeySecret string

// SWA Free — managed Functions in /api are included.
// No system-assigned identity: verified against Microsoft Learn (2026-07-11)
// that Azure Static Web Apps *managed functions* support neither Key Vault
// app-setting references NOR managed identity, on ANY plan (Standard
// included) — both are explicitly listed as unavailable; only Bring-Your-Own
// Functions gets them. So an identity here would do nothing for the /api app
// settings below — FOLLOWUPS #2 is fixed at the bicep/deploy-time layer
// instead (see swaSettings).
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
// STORAGE_CONNECTION_STRING / FUNC_MASTER_KEY are resolved from Key Vault at
// DEPLOY TIME (main.bicep's `keyVault.getSecret(...)` calls, fed in via the
// two @secure() params above) and baked in as plain app settings on EVERY
// infra deploy. This is the fix for FOLLOWUPS #2 ("any infra deploy wipes
// these settings") — because the correct current value now comes from this
// template every time, `az deployment group create` SETS it instead of
// wiping it. It is deliberately NOT a live runtime Key Vault reference
// (`@Microsoft.KeyVault(SecretUri=...)`) — verified that managed-functions
// SWAs cannot resolve those (see the note on `swa` above). To rotate either
// secret: update it in Key Vault (scripts/seed-swa-secrets.sh) and redeploy
// infra to pick up the new value.
resource swaSettings 'Microsoft.Web/staticSites/config@2022-03-01' = {
  parent: swa
  name: 'appsettings'
  properties: {
    APPLICATIONINSIGHTS_CONNECTION_STRING: appInsightsConnectionString
    STORAGE_ACCOUNT_NAME: storageAccountName
    FUNCTION_APP_NAME: 'func-pfauto'
    STORAGE_CONNECTION_STRING: storageConnectionStringSecret
    FUNC_MASTER_KEY: funcMasterKeySecret
  }
}

output staticWebAppName string = swa.name
output defaultHostname string = swa.properties.defaultHostname
