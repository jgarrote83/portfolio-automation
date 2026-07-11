param location string
param keyVaultName string

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'           // standard tier — no HSM, ~$0 at our call volume
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true  // RBAC only, no legacy access policies
    enableSoftDelete: true
    softDeleteRetentionInDays: 7   // minimum allowed
    publicNetworkAccess: 'Enabled'
    // Lets main.bicep resolve secrets at DEPLOY TIME via the `getSecret()` bicep
    // function (SWA hardening batch, FOLLOWUPS #2) — required because Azure
    // Static Web Apps *managed functions* support neither Key Vault app-setting
    // references nor managed identity (verified against Microsoft Learn 2026-07-11:
    // both are explicitly "not available" for managed-functions SWAs, on any plan —
    // only Bring-Your-Own-Functions gets them). getSecret() sidesteps that platform
    // wall entirely: it resolves in the ARM/Bicep deployment itself (the deploying
    // principal needs Microsoft.KeyVault/vaults/deploy/action, bundled in
    // Contributor/Owner — CI already has this), never at SWA runtime, so it works
    // regardless of the managed-functions restriction.
    enabledForTemplateDeployment: true
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
