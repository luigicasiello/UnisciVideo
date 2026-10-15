$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Installazione dipendenze..." -ForegroundColor Cyan
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Installazione delle dipendenze non riuscita (codice $LASTEXITCODE)." }

$ffmpeg = python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
if (-not (Test-Path -LiteralPath $ffmpeg)) { throw "FFmpeg non trovato nel pacchetto imageio-ffmpeg." }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "bin") | Out-Null
Copy-Item -LiteralPath $ffmpeg -Destination (Join-Path $PSScriptRoot "bin\ffmpeg.exe") -Force

$defaultDist = Join-Path $PSScriptRoot "dist"
$defaultExe = Join-Path $defaultDist "UnisciVideo.exe"
$defaultRunning = Get-CimInstance Win32_Process -Filter "Name = 'UnisciVideo.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $defaultExe }
$distPath = if ($defaultRunning) { Join-Path $defaultDist "release" } else { $defaultDist }
$outputExe = Join-Path $distPath "UnisciVideo.exe"
$targetRunning = Get-CimInstance Win32_Process -Filter "Name = 'UnisciVideo.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $outputExe }
if ($targetRunning) { throw "Anche la versione di destinazione è in esecuzione: $outputExe" }

Write-Host "Creazione dell'eseguibile standalone..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --distpath $distPath "UnisciVideo.spec"
if ($LASTEXITCODE -ne 0) { throw "Creazione dell'eseguibile non riuscita (codice $LASTEXITCODE)." }

$signingCertificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq "CN=UnisciVideo Local Publisher" -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
$trustedRoot = if ($signingCertificate) {
    Get-ChildItem "Cert:\CurrentUser\Root\$($signingCertificate.Thumbprint)" -ErrorAction SilentlyContinue
}
$trustedPublisher = if ($signingCertificate) {
    Get-ChildItem "Cert:\CurrentUser\TrustedPublisher\$($signingCertificate.Thumbprint)" -ErrorAction SilentlyContinue
}
if ($signingCertificate -and $trustedRoot -and $trustedPublisher) {
    & (Join-Path $PSScriptRoot "sign-executable.ps1") -Path $outputExe
} else {
    Write-Host "Certificato locale attendibile non configurato: l'eseguibile non è stato firmato." -ForegroundColor Yellow
    Write-Host "Per configurarlo, vedere README.md." -ForegroundColor DarkCyan
}

Write-Host ""
Write-Host "Eseguibile creato in: $outputExe" -ForegroundColor Green
