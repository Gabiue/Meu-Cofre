# Instalador do meu-cofre no Windows (PowerShell 5+, sem dependências): atalho no Menu Iniciar e ícone.
#
#   powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1            # instala ou atualiza (rode de novo depois de um `git pull`)
#   powershell -ExecutionPolicy Bypass -File scripts\instalar.ps1 -Remover   # apaga só o atalho, o ícone e o launcher
#
# Diferente do Linux, não há cópia estável por git worktree: o atalho roda a própria pasta do projeto
# (trocar de branch nela muda o app; mantenha a pasta na main).
# O banco fica em %APPDATA%\meu-cofre\meu-cofre.db, a menos que MEU_COFRE_DB esteja definida ou já exista data\meu-cofre.db.
param([switch]$Remover)
$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Dados = Join-Path $env:APPDATA 'meu-cofre'
$Ico = Join-Path $Dados 'meu-cofre.ico'
$Launcher = Join-Path $Dados 'abrir.vbs'
$Atalho = Join-Path ([Environment]::GetFolderPath('Programs')) 'meu-cofre.lnk'

if ($Remover) {
    Remove-Item -Force -ErrorAction SilentlyContinue $Atalho, $Ico, $Launcher
    Write-Host 'atalho, ícone e launcher removidos. Seus dados ficam (apague à mão se quiser):'
    Write-Host "  banco: $Dados (ou $Repo\data, se já usava)"
    exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'erro: o uv não está instalado. Instale e rode este script de novo:' -ForegroundColor Red
    Write-Host '  winget install astral-sh.uv'
    Write-Host '  https://docs.astral.sh/uv/'
    exit 1
}

Push-Location $Repo
try {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'uv sync falhou' }

    New-Item -ItemType Directory -Force $Dados | Out-Null
    # O próprio PySide6 do ambiente converte o SVG em .ico (256 px; o Windows reduz pros outros tamanhos).
    $conversor = Join-Path $env:TEMP 'meu-cofre-icone.py'
    @'
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QImageWriter, QPainter
from PySide6.QtSvg import QSvgRenderer

app = QGuiApplication(sys.argv)
img = QImage(256, 256, QImage.Format_ARGB32)
img.fill(Qt.transparent)
pintor = QPainter(img)
QSvgRenderer(sys.argv[1]).render(pintor)
pintor.end()
escritor = QImageWriter(sys.argv[2], b"ico")
sys.exit(0 if escritor.write(img) else escritor.errorString())
'@ | Set-Content -Encoding UTF8 $conversor
    $env:QT_QPA_PLATFORM = 'offscreen'
    uv run python $conversor (Join-Path $Repo 'app\meu_cofre\ui\icone.svg') $Ico
    if ($LASTEXITCODE -ne 0) { throw 'não consegui gerar o ícone' }
    Remove-Item -Force $conversor
    Remove-Item Env:QT_QPA_PLATFORM
} finally {
    Pop-Location
}

$Pythonw = Join-Path $Repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "não achei $Pythonw (o uv sync criou o .venv?)" }

# Só fixa o banco quando veio da variável ou já existe em data\ (instalação antiga); senão a config usa o %APPDATA%.
$Banco = $env:MEU_COFRE_DB
if (-not $Banco -and (Test-Path (Join-Path $Repo 'data\meu-cofre.db'))) { $Banco = Join-Path $Repo 'data\meu-cofre.db' }

$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($Atalho)
if ($Banco) {
    # Um .lnk não carrega variável de ambiente: um .vbs a define e abre o pythonw sem janela de console (estilo 0).
    @(
        'Set s = CreateObject("WScript.Shell")'
        "s.CurrentDirectory = ""$Repo"""
        "s.Environment(""PROCESS"")(""MEU_COFRE_DB"") = ""$Banco"""
        ('s.Run """' + $Pythonw + '"" -m meu_cofre.ui.app", 0, False')  # aspas duplas dentro de string VBScript são ""
    ) | Set-Content -Encoding Default $Launcher
    $lnk.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $lnk.Arguments = "`"$Launcher`""
} else {
    Remove-Item -Force -ErrorAction SilentlyContinue $Launcher
    $lnk.TargetPath = $Pythonw
    $lnk.Arguments = '-m meu_cofre.ui.app'
}
$lnk.WorkingDirectory = $Repo
$lnk.IconLocation = $Ico
$lnk.Description = 'Controle financeiro local: seus dados nunca saem do PC'
$lnk.Save()

Write-Host "meu-cofre instalado: atalho em $Atalho"
if ($Banco) { Write-Host "banco: $Banco" } else { Write-Host "banco: $Dados\meu-cofre.db" }
