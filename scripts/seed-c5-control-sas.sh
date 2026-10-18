#!/usr/bin/env bash
# Seed the EasyGrid Controls card's write credential into Key Vault (kv-pfauto-prod).
#
# The SWA managed API has NO managed identity (Azure Static Web Apps managed functions support neither managed identity nor Key Vault
# references, on any plan), so a container-scoped RBAC role is impossible. Instead the Controls card writes through TWO BLOB-SCOPED SAS
# URLs, one for c5-state/kill-switch.json and one for c5-state/realign-request.json, so the credential itself cannot touch any other blob.
# Both are signed against the container's stored access policy `easygrid-controls`, so deleting that policy revokes them at once.
#
# main.bicep reads the two secrets with getSecret() at DEPLOY TIME: RUN THIS BEFORE THE FIRST INFRA DEPLOY THAT INCLUDES THEM, or that
# deploy fails. Re-run it to rotate (the policy expiry is one year), then redeploy infra.
#
# Everything is scoped to rg-portfolio-automation-prod / EasyGridsProduction (CLAUDE.md rule 10). No value is ever printed.
#
# Usage (signed in as jgarrote@easygrids.com):   ./scripts/seed-c5-control-sas.sh
set -euo pipefail

SUB="EasyGridsProduction"
RG="rg-portfolio-automation-prod"
ACCOUNT="stpfautoprod"
VAULT="kv-pfauto-prod"
CONTAINER="c5-state"
POLICY="easygrid-controls"
EXPIRY="$(date -u -d '+365 days' +%Y-%m-%dT%H:%MZ 2>/dev/null || python -c 'import datetime as d;print((d.datetime.utcnow()+d.timedelta(days=365)).strftime("%Y-%m-%dT%H:%MZ"))')"

[ "$(az account show --query user.name -o tsv)" = "jgarrote@easygrids.com" ] || { echo "sign in as jgarrote@easygrids.com first" >&2; exit 1; }
[ "$(az storage account show --name "$ACCOUNT" --subscription "$SUB" --query resourceGroup -o tsv)" = "$RG" ] || { echo "$ACCOUNT is not in $RG" >&2; exit 1; }

KEY="$(az storage account keys list --account-name "$ACCOUNT" --resource-group "$RG" --subscription "$SUB" --query '[0].value' -o tsv)"
export AZURE_STORAGE_ACCOUNT="$ACCOUNT" AZURE_STORAGE_KEY="$KEY"

az storage container create --name "$CONTAINER" --output none
# NOTE: `az storage container policy show` exits 0 even when the policy does not exist (verified 2026-10-18), so it cannot be used
# as an existence probe; look the name up in the policy list instead.
if [ "$(az storage container policy list --container-name "$CONTAINER" --query "contains(keys(@), '$POLICY')" -o tsv)" = "true" ]; then
  az storage container policy update --container-name "$CONTAINER" --name "$POLICY" --permissions rcw --expiry "$EXPIRY" --output none
else
  az storage container policy create --container-name "$CONTAINER" --name "$POLICY" --permissions rcw --expiry "$EXPIRY" --output none
fi

for pair in "kill-switch.json:swa-c5-killswitch-sas-url" "realign-request.json:swa-c5-realign-sas-url"; do
  BLOB="${pair%%:*}"; SECRET="${pair##*:}"
  URL="$(az storage blob generate-sas --container-name "$CONTAINER" --name "$BLOB" --policy-name "$POLICY" --https-only --full-uri -o tsv)"
  az keyvault secret set --vault-name "$VAULT" --subscription "$SUB" --name "$SECRET" --value "$URL" --output none
  echo "seeded $SECRET (blob $CONTAINER/$BLOB, policy $POLICY, expires $EXPIRY)"
done
unset KEY AZURE_STORAGE_KEY URL
echo "done. Now redeploy infra so swa-pfauto picks the two URLs up."
