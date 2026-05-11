# Deploy Portfolio Automation infrastructure to EasyGridsProduction
# Scope: rg-portfolio-automation-prod only — no other resource groups are touched.
#
# Usage:  .\infra\deploy.ps1
#         .\infra\deploy.ps1 -WhatIf    # dry-run, shows what would change

param(
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'

$RG       = 'rg-portfolio-automation-prod'
$LOCATION = 'eastus'
$TEMPLATE = "$PSScriptRoot/main.bicep"
$PARAMS   = "$PSScriptRoot/parameters.prod.json"
$DEPLOY   = "pfauto-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# Verify we are on the right subscription before touching anything
$sub = (az account show --query name -o tsv)
if ($sub -ne 'EasyGridsProduction') {
  Write-Error "Wrong subscription: '$sub'. Run: az account set --subscription EasyGridsProduction"
  exit 1
}

Write-Host "Subscription : $sub" -ForegroundColor Cyan
Write-Host "Resource group: $RG ($LOCATION)" -ForegroundColor Cyan

# Create resource group if it does not exist (idempotent)
az group create --name $RG --location $LOCATION --output none
Write-Host "Resource group ready." -ForegroundColor Green

if ($WhatIf) {
  Write-Host "`nRunning what-if (no changes will be made)..." -ForegroundColor Yellow
  az deployment group what-if `
    --resource-group $RG `
    --template-file  $TEMPLATE `
    --parameters     $PARAMS `
    --name           $DEPLOY
} else {
  Write-Host "`nDeploying..." -ForegroundColor Yellow
  az deployment group create `
    --resource-group $RG `
    --template-file  $TEMPLATE `
    --parameters     $PARAMS `
    --name           $DEPLOY `
    --output         table
  Write-Host "`nDone. Outputs:" -ForegroundColor Green
  az deployment group show `
    --resource-group $RG `
    --name           $DEPLOY `
    --query          properties.outputs `
    --output         table
}
