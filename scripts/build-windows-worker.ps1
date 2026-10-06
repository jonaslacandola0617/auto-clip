param(
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $repoRoot ".venv\Scripts\python.exe"
}
$pythonPath = [System.IO.Path]::GetFullPath($PythonExecutable)
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Release Python was not found at $pythonPath. Pass -PythonExecutable or create .venv."
}

$releaseRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "release-output\worker"))
$resourceRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "src-tauri\resources\runtime\worker"))
foreach ($path in @($releaseRoot, $resourceRoot)) {
    if (-not $path.StartsWith($repoRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Unsafe release path: $path"
    }
}

if (Test-Path -LiteralPath $releaseRoot) {
    Remove-Item -LiteralPath $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
New-Item -ItemType Directory -Path $resourceRoot -Force | Out-Null
Get-ChildItem -LiteralPath $resourceRoot -Force | Where-Object { $_.Name -ne ".gitkeep" } | ForEach-Object {
    $resolved = [System.IO.Path]::GetFullPath($_.FullName)
    if (-not $resolved.StartsWith($resourceRoot + [System.IO.Path]::DirectorySeparatorChar)) {
        throw "Unsafe runtime cleanup path: $resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
}

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name autoclip-worker `
    --paths (Join-Path $repoRoot "src") `
    --distpath (Join-Path $releaseRoot "dist") `
    --workpath (Join-Path $releaseRoot "build") `
    --specpath $releaseRoot `
    --add-data "$repoRoot\src\autoclip\assets\detectors;autoclip\assets\detectors" `
    --hidden-import faster_whisper `
    --hidden-import mediapipe `
    --hidden-import cv2 `
    --hidden-import static_ffmpeg `
    --collect-binaries ctranslate2 `
    --collect-binaries mediapipe `
    --collect-data mediapipe `
    --collect-data static_ffmpeg `
    --exclude-module matplotlib `
    --exclude-module pytest `
    --exclude-module sounddevice `
    --exclude-module tkinter `
    --hidden-import numpy `
    (Join-Path $repoRoot "scripts\worker_entry.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller worker build failed." }

$builtRoot = Join-Path $releaseRoot "dist\autoclip-worker"
Get-ChildItem -LiteralPath $builtRoot -Force | Copy-Item -Destination $resourceRoot -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $resourceRoot "autoclip-worker.exe") -PathType Leaf)) {
    throw "The packaged worker executable was not staged."
}

$size = (Get-ChildItem -LiteralPath $resourceRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
$hash = (Get-FileHash -LiteralPath (Join-Path $resourceRoot "autoclip-worker.exe") -Algorithm SHA256).Hash
Write-Output "Packaged worker staged: $resourceRoot"
Write-Output "Worker runtime bytes: $size"
Write-Output "Worker executable SHA-256: $hash"
