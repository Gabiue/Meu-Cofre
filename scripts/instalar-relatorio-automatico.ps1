# Instala (ou remove) a tarefa agendada que gera o relatório do mês passado no dia 1 de cada mês, às 8h.
#
#   powershell -ExecutionPolicy Bypass -File scripts\instalar-relatorio-automatico.ps1            # instala/atualiza
#   powershell -ExecutionPolicy Bypass -File scripts\instalar-relatorio-automatico.ps1 -Remover   # apaga a tarefa e o wrapper
#
# Roda `python -m meu_cofre.relatorio_automatico` (o `meu-cofre relatorio`) na pasta do projeto: rode scripts\instalar.ps1 antes.
# New-ScheduledTaskTrigger não tem gatilho mensal, então a tarefa é registrada por XML (CalendarTrigger mensal, dia 1).
# StartWhenAvailable: se o PC estava desligado às 8h, roda assim que ligar.
# Passo opcional depois de gerar: se existir %APPDATA%\meu-cofre\depois-do-relatorio.ps1 (você cria, ex.: mandar por email),
# ele é chamado com o caminho do arquivo como primeiro argumento. O app em si nunca envia nada.
param([switch]$Remover)
$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dados = Join-Path $env:APPDATA 'meu-cofre'
$Gancho = Join-Path $Dados 'depois-do-relatorio.ps1'
$Wrapper = Join-Path $Dados 'relatorio-automatico.ps1'
$Tarefa = 'meu-cofre-relatorio'

if ($Remover) {
    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item -Force -ErrorAction SilentlyContinue $Wrapper
    Write-Host 'tarefa do relatório removida'
    exit 0
}

$Python = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Write-Host "erro: $Python não existe; rode antes: powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1" -ForegroundColor Red
    exit 1
}

# Só fixa o banco se veio da variável ou existe em data\ (instalação antiga); senão a config usa o %APPDATA%.
$Banco = $env:MEU_COFRE_DB
if (-not $Banco -and (Test-Path (Join-Path $Repo 'data\meu-cofre.db'))) { $Banco = Join-Path $Repo 'data\meu-cofre.db' }

function Aspas($texto) { "'" + $texto.Replace("'", "''") + "'" }  # literal PowerShell entre aspas simples

# O wrapper gera o relatório e, se o gancho existir, entrega o caminho do arquivo a ele.
New-Item -ItemType Directory -Force $Dados | Out-Null
$linhas = @('$ErrorActionPreference = ''Stop''', '[Console]::OutputEncoding = [Text.Encoding]::UTF8')  # o Python escreve utf-8
if ($Banco) { $linhas += '$env:MEU_COFRE_DB = ' + (Aspas $Banco) }
$linhas += 'Set-Location ' + (Aspas $Repo)
$linhas += '$arquivo = & ' + (Aspas $Python) + ' -m meu_cofre.relatorio_automatico | Select-Object -Last 1'
$linhas += '$gancho = ' + (Aspas $Gancho)
$linhas += 'if ($arquivo -and (Test-Path $gancho)) { & $gancho $arquivo }'
$linhas | Set-Content -Encoding UTF8 $Wrapper

$esc = [System.Security.SecurityElement]::Escape("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Wrapper`"")
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByMonth>
        <DaysOfMonth><Day>1</Day></DaysOfMonth>
        <Months><January/><February/><March/><April/><May/><June/><July/><August/><September/><October/><November/><December/></Months>
      </ScheduleByMonth>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <StartWhenAvailable>true</StartWhenAvailable>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$esc</Arguments>
      <WorkingDirectory>$([System.Security.SecurityElement]::Escape($Repo))</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
Register-ScheduledTask -TaskName $Tarefa -Xml $xml -Force | Out-Null
Write-Host 'tarefa instalada: relatório do mês passado todo dia 1, às 8h (ou assim que o PC ligar, se estava desligado)'
Write-Host "gancho opcional: $Gancho"
