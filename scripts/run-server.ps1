#Requires -Version 5.1
<# Inicia o servidor Jarvis Kitchen usando o .venv do projeto. #>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { throw "Execute scripts\setup-server.ps1 primeiro." }
& $venvPython -m server.main
exit $LASTEXITCODE
