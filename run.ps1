$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    py app.py start @args
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    python app.py start @args
    exit $LASTEXITCODE
}

Write-Error "Python launcher (py) or python.exe was not found in PATH."
