param(
    [string]$FfmpegExecutable = "ffmpeg"
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$fixtureRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "release-fixtures"))
if (-not $fixtureRoot.StartsWith($repoRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Unsafe fixture path."
}
New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
$speechPath = Join-Path $fixtureRoot "autoclip-rights-safe-speech.wav"
$videoPath = Join-Path $fixtureRoot "autoclip-rights-safe-source.mp4"

Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speaker.SetOutputToWaveFile($speechPath)
    $speaker.Speak("AutoClip release validation. This original synthetic recording tests transcription, captions, preview, rendering, export, close, reopen, and cache reuse. The quick blue camera follows the bright subject across the frame.")
} finally {
    $speaker.Dispose()
}

& $FfmpegExecutable -y -f lavfi -i "testsrc2=size=1280x720:rate=30" -i $speechPath -shortest -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 48000 -ac 2 $videoPath
if ($LASTEXITCODE -ne 0) { throw "Rights-safe media generation failed." }
Write-Output $videoPath
