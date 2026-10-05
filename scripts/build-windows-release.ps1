param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$workerScript = Join-Path $PSScriptRoot "build-windows-worker.ps1"
& $workerScript -PythonExecutable $PythonExecutable
if ($LASTEXITCODE -ne 0) { throw "Worker staging failed." }

Push-Location $repoRoot
try {
    & npm run tauri build
    if ($LASTEXITCODE -ne 0) { throw "Tauri release build failed." }
} finally {
    Pop-Location
}

$bundleRoot = Join-Path $repoRoot "src-tauri\target\release\bundle\nsis"
$installers = Get-ChildItem -LiteralPath $bundleRoot -Filter "*.exe" -File
if (-not $installers) { throw "No NSIS installer was produced." }
$installers | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    Write-Output "Installer: $($_.FullName)"
    Write-Output "Installer bytes: $($_.Length)"
    Write-Output "Installer SHA-256: $hash"
}
