#Requires -Version 5.1
<# Compila a interface do cliente (TypeScript -> client/public/js). Requer Node.js. #>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "client")
if (-not (Test-Path "node_modules")) {
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install falhou" }
}
npm run build
exit $LASTEXITCODE
