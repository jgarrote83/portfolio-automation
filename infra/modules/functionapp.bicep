// Flex Consumption Function App.
//
// Why Flex over Linux Consumption (Y1):
//   - Linux Consumption is on the retirement path (2028).
//   - Flex uses identity-based storage end-to-end — no shared key, no
//     Azure Files content share, no WEBSITE_CONTENTAZUREFILECONNECTIONSTRING.
//     This eliminates the SSL/cryptography host-start failure mode we hit
//     twice on Y1 (see CLAUDE.md "Deployment lessons").
//   - 40-min timeout ceiling (vs Y1's 10-min cap).
//   - Per-instance memory + scale knobs live in functionAppConfig.
//
// Deployment model: one-deploy from a blob container ('deployment').
// The MI is granted Storage Blob Data Owner at the account scope by
// storage-roles.bicep, which covers reads of the package blob.

param location string
param functionAppName string
param appServicePlanName string
param storageAccountName string
param appInsightsConnectionString string
param keyVaultUri string

@description('Blob container that holds the deployment .zip package.')
param deploymentContainerName string = 'deployment'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: appServicePlanName
  location: location
  kind: 'functionapp'
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageAccount.properties.primaryEndpoints.blob}${deploymentContainerName}'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: {
        name: 'python'
        version: '3.11'
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        // Identity-based AzureWebJobsStorage (no shared key).
        { name: 'AzureWebJobsStorage__accountName',          value: storageAccountName }
        { name: 'AzureWebJobsStorage__credential',           value: 'managedidentity' }
        // Monitoring + app config.
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING',     value: appInsightsConnectionString }
        { name: 'KEY_VAULT_URI',                             value: keyVaultUri }
        { name: 'STORAGE_ACCOUNT_NAME',                      value: storageAccountName }
        // Foundry / Claude endpoint.
        { name: 'FOUNDRY_ENDPOINT',                          value: 'https://resource-portfolio-analysis.services.ai.azure.com/anthropic/v1/messages?api-version=2025-04-01-preview' }
        { name: 'FOUNDRY_MODEL',                             value: 'claude-sonnet-4-6' }
        // Runtime knobs — kept in IaC so an infra redeploy does NOT wipe them.
        // (They were previously applied post-deploy; that fragility silently
        // disabled the pipeline after the Flex migration — see CLAUDE.md.)
        //   TZ: crons are ET-local (collector 09:00 ET, auto_executor 09:35 ET).
        //       Without it crons run UTC and auto_executor fires pre-market.
        { name: 'TZ',                                        value: 'America/New_York' }
        //   Paper-only auto-execute at 09:35 ET. NOTE: this overrides the
        //   "Phase 1 clean 30+ days before Phase 2" gate — auto-trading is on.
        { name: 'AUTO_EXECUTE_ENABLED',                      value: 'true' }
        //   Intraday catalyst Flex engine (flex_intraday, every 15 min, is_open-gated).
        //   Places live (paper) broker orders. Enabled 2026-07-07 (FOLLOWUPS #33
        //   residual (iv) — MU flex position unmanaged) after dry-run validation
        //   (POST /api/flex {"dry_run":true}). NOTE: the engine only manages symbols
        //   in flex-ledger/ledger.json — pre-engine positions (MU) need a ledger row
        //   before it will manage them. FLEX_* knobs default in src/flex/config.py;
        //   override here only if tuning.
        { name: 'FLEX_ENABLED',                              value: 'true' }
        //   DayTrade Lab (daytrade_manage, every 1 min, clock/window-gated).
        //   Ships OFF — places live (paper) broker orders. Flip to 'true' only
        //   after dry-run validation (POST /api/daytrade {"dry_run":true}) AND
        //   resolving consolidated_source (docs/specs/DayTrade_Lab_v0.1.md §2).
        { name: 'DAYTRADE_ENABLED',                          value: 'false' }
        //   Analyzer Claude call on large snapshots can exceed the old 10-min
        //   Consumption cap; Flex allows more. Backstops host.json functionTimeout.
        { name: 'AzureFunctionsJobHost__functionTimeout',    value: '00:40:00' }
        // NOTE — settings intentionally NOT set here (Flex defaults / managed by platform):
        //   FUNCTIONS_EXTENSION_VERSION   (Flex auto-pins ~4)
        //   FUNCTIONS_WORKER_RUNTIME      (moved to functionAppConfig.runtime)
        //   PYTHON_ISOLATE_WORKER_DEPENDENCIES (always-on in Flex)
        //   WEBSITE_CONTENTAZUREFILECONNECTIONSTRING / WEBSITE_CONTENTSHARE (no Azure Files)
        //   WEBSITE_RUN_FROM_PACKAGE       (Flex deployment uses functionAppConfig.deployment)
      ]
    }
  }
}

output principalId string = functionApp.identity.principalId
output functionAppName string = functionApp.name
