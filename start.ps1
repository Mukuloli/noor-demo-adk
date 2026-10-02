$ErrorActionPreference = 'Stop'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonExe)) {
    throw 'Create .venv and install requirements.txt first. See README.md.'
}
Push-Location $PSScriptRoot
try {
    & $pythonExe -m uvicorn server:app --host 127.0.0.1 --port 8001
} finally {
    Pop-Location
}
