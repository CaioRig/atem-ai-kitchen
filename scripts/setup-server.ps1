#Requires -Version 5.1
<#
  Prepara o servidor Jarvis Kitchen nesta maquina (Windows):
  - cria .venv com Python 3.12
  - instala dependencias (requirements-dev.txt)
  - gera config/settings.toml com token aleatorio e IP da LAN, se ainda nao existir
  Idempotente: pode ser executado varias vezes.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Python312 {
    if ($env:LOCALAPPDATA) {
        $managed = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.12-64\python.exe"
        if (Test-Path $managed) { return $managed }
    }
    try {
        $exe = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    } catch { }
    throw "Python 3.12 nao encontrado. Instale com 'py install 3.12' (a partir do PowerShell, nao do Git Bash)."
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $python = Find-Python312
    Write-Host "Criando .venv com $python"
    & $python -m venv (Join-Path $root ".venv")
}

Write-Host "Instalando dependencias..."
& $venvPython -m pip install --disable-pip-version-check -q -r (Join-Path $root "requirements-dev.txt")

$settings = Join-Path $root "config\settings.toml"
if (-not (Test-Path $settings)) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""

    $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
        Sort-Object -Property InterfaceMetric |
        Select-Object -First 1).IPAddress
    if (-not $ip) { $ip = "127.0.0.1" }

    $content = (Get-Content (Join-Path $root "config\settings.example.toml") -Raw)
    $content = $content.Replace("TROQUE-ESTE-TOKEN", $token).Replace("192.168.1.6", $ip)
    [System.IO.File]::WriteAllText($settings, $content, (New-Object System.Text.UTF8Encoding $false))
    Write-Host ""
    Write-Host "config/settings.toml criado (bind_host=$ip)."
    Write-Host "URL do quiosque: https://${ip}:8800/#token=$token"
} else {
    Write-Host "config/settings.toml ja existe; mantido."
}

Write-Host "Pronto. Proximos passos: .\scripts\build-client.ps1 e .\scripts\run-server.ps1"
