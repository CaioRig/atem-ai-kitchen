#Requires -Version 5.1
<#
  Gera certificado TLS autoassinado para o servidor Jarvis Kitchen em config\certs\.
  O navegador so libera o microfone em HTTPS (ou localhost); este certificado cobre o IP
  de [server] bind_host em config\settings.toml (SAN), alem de localhost e 127.0.0.1.
  Usa o openssl que acompanha o Git for Windows (ou qualquer openssl no PATH).
  Idempotente: so sobrescreve um par existente com -Force.
#>
param(
    [string]$Ip,
    [int]$Days = 3650,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
# Sem ".EXE" em PATHEXT o PowerShell nao reconhece .exe como executavel (acontece em shells minimos).
if (-not $env:PATHEXT -or $env:PATHEXT -notmatch '(?i)\.EXE') { $env:PATHEXT = ".COM;.EXE;.BAT;.CMD;.PS1;$env:PATHEXT" }
$root = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $root "config\certs"
$crt = Join-Path $certDir "server.crt"
$key = Join-Path $certDir "server.key"

if ((Test-Path $crt) -and (Test-Path $key) -and -not $Force) {
    Write-Host "Certificado ja existe em $crt (use -Force para regerar)."
    exit 0
}

if (-not $Ip) {
    $settings = Join-Path $root "config\settings.toml"
    if (Test-Path $settings) {
        $m = [regex]::Match((Get-Content $settings -Raw), '(?m)^\s*bind_host\s*=\s*"([^"]+)"')
        if ($m.Success) { $Ip = $m.Groups[1].Value }
    }
}
if (-not $Ip) { throw "Informe -Ip <ip-da-lan> ou defina bind_host em config\settings.toml." }

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    $candidates = @()
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        $gitRoot = Split-Path -Parent (Split-Path -Parent $git.Source)   # ...\Git\cmd\git.exe -> ...\Git
        if ((Split-Path -Leaf $gitRoot) -eq "mingw64") { $gitRoot = Split-Path -Parent $gitRoot }
        $candidates += (Join-Path $gitRoot "usr\bin\openssl.exe"), (Join-Path $gitRoot "mingw64\bin\openssl.exe")
    }
    $bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "C:\Program Files", "C:\Program Files (x86)")
    if ($env:LOCALAPPDATA) { $bases += (Join-Path $env:LOCALAPPDATA "Programs") }
    foreach ($base in $bases) {
        if ($base) { $candidates += (Join-Path $base "Git\usr\bin\openssl.exe"), (Join-Path $base "Git\mingw64\bin\openssl.exe") }
    }
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $openssl = Get-Command $candidate; break }
    }
}
if (-not $openssl) { throw "openssl nao encontrado. Instale o Git for Windows ou adicione o openssl ao PATH." }

New-Item -ItemType Directory -Force -Path $certDir | Out-Null

$san = "DNS:localhost,IP:127.0.0.1"
if ($Ip -ne "127.0.0.1" -and $Ip -ne "localhost") { $san = "IP:$Ip,$san" }

# openssl escreve o progresso em stderr; com $ErrorActionPreference=Stop isso viraria excecao.
$exe = $openssl.Source
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $exe req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes `
    -keyout $key -out $crt -days $Days -sha256 `
    -subj "/CN=Jarvis Kitchen ($Ip)" `
    -addext "subjectAltName=$san" `
    -addext "keyUsage=digitalSignature,keyEncipherment" `
    -addext "extendedKeyUsage=serverAuth" 2>&1 | Out-Null
$code = $LASTEXITCODE
$ErrorActionPreference = $prev
if ($code -ne 0) { throw "openssl falhou ($code)" }

Write-Host ""
Write-Host "Certificado: $crt"
Write-Host "Chave:       $key"
Write-Host "Validade:    $Days dias, SAN = $san"
Write-Host "Em config\settings.toml, [server] deve ter:"
Write-Host '  tls_certfile = "config/certs/server.crt"'
Write-Host '  tls_keyfile  = "config/certs/server.key"'
Write-Host "No navegador do quiosque, aceite o aviso de certificado uma vez ou importe server.crt"
Write-Host "em 'Autoridades de certificacao raiz confiaveis'. Regere ao mudar o IP (-Force)."
