#Requires -Version 5.1
<# Inicializacao no logon do usuario atual. -WhatIf mostra sem registrar. #>
[CmdletBinding(SupportsShouldProcess)]
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskName = 'Luna Kitchen Server'
$root = Split-Path -Parent $PSScriptRoot
if ($Remove) {
    if ($PSCmdlet.ShouldProcess($taskName, 'Remover inicializacao automatica')) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    return
}
foreach ($relative in @('.venv\Scripts\python.exe', 'config\settings.toml', 'client\public\js\main.js')) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative))) {
        throw "Arquivo necessario ausente: $relative"
    }
}
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { throw "A tarefa $taskName ja existe. Inspecione-a antes de substituir." }
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute (Join-Path $root '.venv\Scripts\python.exe') -Argument '-m server.main' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($PSCmdlet.ShouldProcess($taskName, 'Registrar servidor no logon (sem iniciar agora)')) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Luna local; inicia 30 segundos apos logon e tenta reiniciar em caso de falha.' | Out-Null
}
