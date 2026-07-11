#!/usr/bin/env bash
# Seed the swa-pfauto secrets into Key Vault (kv-pfauto-prod).
#
# Part of the SWA hardening batch (FOLLOWUPS #2/#3/#4). staticwebapp.bicep
# resolves these at DEPLOY TIME via the bicep `getSecret()` function (Azure
# Static Web Apps managed functions support neither Key Vault app-setting
# references nor managed identity, on any plan — verified against Microsoft
# Learn 2026-07-11), so seeding them here is a one-time (or rotate-as-needed)
# step; every subsequent infra deploy reads the CURRENT secret value itself.
#
# Idempotent: `az keyvault secret set` upserts, so re-running with the same
# value is a no-op in effect (it does create a new secret VERSION each time —
# harmless, Key Vault keeps prior versions until purge).
#
# Does NOT invent values. Reads from env vars and fails loudly if unset:
#   SWA_STORAGE_CONNECTION_STRING   -- `az storage account show-connection-string
#                                       --name stpfautoprod -g rg-portfolio-automation-prod
#                                       --query connectionString -o tsv`
#   SWA_FUNC_MASTER_KEY             -- `az functionapp keys list --name func-pfauto
#                                       -g rg-portfolio-automation-prod --query masterKey -o tsv`
#
# Usage:
#   SWA_STORAGE_CONNECTION_STRING="..." SWA_FUNC_MASTER_KEY="..." ./scripts/seed-swa-secrets.sh
#
# Note (2026-07-11 decision): the AAD custom-app-registration path (Task C's
# original spec) was NOT taken — Entra auth uses the Free-tier PRECONFIGURED
# provider + an invitation-based "owner" role instead (no app registration, no
# client secret, ever). So there are no swa-aad-client-id / swa-aad-client-secret
# entries here; if that decision is ever revisited, add them the same way.

set -euo pipefail

VAULT_NAME="${VAULT_NAME:-kv-pfauto-prod}"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

[ -n "${SWA_STORAGE_CONNECTION_STRING:-}" ] || fail "SWA_STORAGE_CONNECTION_STRING is not set — refusing to seed a blank/guessed secret."
[ -n "${SWA_FUNC_MASTER_KEY:-}" ] || fail "SWA_FUNC_MASTER_KEY is not set — refusing to seed a blank/guessed secret."

echo "Seeding swa-storage-connection-string into $VAULT_NAME ..."
az keyvault secret set \
  --vault-name "$VAULT_NAME" \
  --name swa-storage-connection-string \
  --value "$SWA_STORAGE_CONNECTION_STRING" \
  --output none

echo "Seeding swa-func-master-key into $VAULT_NAME ..."
az keyvault secret set \
  --vault-name "$VAULT_NAME" \
  --name swa-func-master-key \
  --value "$SWA_FUNC_MASTER_KEY" \
  --output none

echo "Done. Verify with:"
echo "  az keyvault secret list --vault-name $VAULT_NAME --query \"[?starts_with(name,'swa-')].name\" -o tsv"
