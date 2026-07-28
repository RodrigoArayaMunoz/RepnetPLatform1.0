$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot "src\backend\compatibilties\.env"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "No se encontro el archivo de entorno: $envPath"
}

$redirectLine = Get-Content -LiteralPath $envPath |
    Where-Object { $_ -match "^\s*ML_REDIRECT_URI\s*=" } |
    Select-Object -First 1

if (-not $redirectLine) {
    throw "ML_REDIRECT_URI no esta configurado en $envPath"
}

$redirectValue = ($redirectLine -split "=", 2)[1].Trim().Trim('"').Trim("'")
$redirectUri = [Uri]$redirectValue

if (-not $redirectUri.IsAbsoluteUri -or -not $redirectUri.Host) {
    throw "ML_REDIRECT_URI no contiene una URL publica valida."
}

$publicUrl = "$($redirectUri.Scheme)://$($redirectUri.Host)"
Write-Host "Iniciando ngrok: $publicUrl -> http://localhost:8000" -ForegroundColor Green

& ngrok http 8000 --url $publicUrl
