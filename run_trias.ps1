# Inicia TRIAS desde su copia local, sin depender de una terminal previamente activada.
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$expectedRoot = Join-Path $env:USERPROFILE "Desktop\dev\SI\TRIAS"

if (-not (Test-Path -LiteralPath $expectedRoot)) {
    throw "No existe la copia local esperada: $expectedRoot"
}
if ($projectRoot -ne (Resolve-Path $expectedRoot).Path) {
    throw "Este script debe ejecutarse desde la copia local: $expectedRoot"
}

$python = Join-Path $projectRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "No encontré el entorno virtual en $python. Recrea o configura venv primero."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    throw "Falta .env. Copia .env.example y configura el token y la invitación."
}

Set-Location -LiteralPath $projectRoot
& $python main.py
