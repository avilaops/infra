#Requires -Version 5.1
<#
.SYNOPSIS
  Instala o inventário de capacidades neste PC Windows e agenda a rodada diária.

.DESCRIPTION
  Copia inventario_capacidades.py para %LOCALAPPDATA%\inventario-capacidades (a cópia
  instalada, que não depende de branch nem de worktree) e registra a tarefa agendada
  "Inventario de capacidades", que roda todo dia às 09:00 e também 10 minutos depois
  do logon. Roda com pythonw, sem janela; o log fica em
  %LOCALAPPDATA%\inventario-capacidades\inventario.log.

  A configuração (webhook, nome da máquina, pastas de projetos) fica em
  %APPDATA%\inventario-capacidades\config.env, fora do Git. Sem ela o script recusa
  rodar.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File inventario-capacidades\instalar-windows.ps1
#>
$ErrorActionPreference = 'Stop'

$destino = Join-Path $env:LOCALAPPDATA 'inventario-capacidades'
$config = Join-Path $env:APPDATA 'inventario-capacidades\config.env'
$script = Join-Path $destino 'inventario_capacidades.py'
$log = Join-Path $destino 'inventario.log'

if (-not (Test-Path $config)) {
    throw "Falta $config com INVENTARIO_WEBHOOK, INVENTARIO_MAQUINA e INVENTARIO_PROJETOS (veja o README)."
}

$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "pythonw.exe não encontrado ao lado de $python."
}

New-Item -ItemType Directory -Force $destino | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'inventario_capacidades.py') -Destination $script -Force

$acao = New-ScheduledTaskAction -Execute $pythonw -Argument "-I `"$script`" --log `"$log`""
$diario = New-ScheduledTaskTrigger -Daily -At '09:00'
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$logon.Delay = 'PT10M'
$opcoes = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'Inventario de capacidades' -Action $acao -Trigger $diario, $logon -Settings $opcoes `
    -Description 'Manda ao n8n o inventario de plugins, skills, hooks e MCPs dos agentes de IA deste PC.' -Force | Out-Null

Write-Output "Instalado em $script"
Write-Output "Tarefa 'Inventario de capacidades' registrada: todo dia as 09:00 e 10 min apos o logon."
Write-Output "Log: $log"
