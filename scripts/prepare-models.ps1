#Requires -Version 5.1
<# Baixa os modelos do openWakeWord para models/openwakeword (manual, com internet). #>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Execute scripts\setup-server.ps1 primeiro." }
& $venvPython (Join-Path $root "scripts\prepare_models.py") @args
exit $LASTEXITCODE
