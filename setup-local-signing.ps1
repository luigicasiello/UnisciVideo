$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$subject = "CN=UnisciVideo Local Publisher"
$friendlyName = "UnisciVideo - firma locale"
$certificateFolder = Join-Path $PSScriptRoot "certificates"
$publicCertificate = Join-Path $certificateFolder "UnisciVideo-Local-Publisher.cer"
$certificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $subject -and $_.NotAfter -gt (Get-Date).AddDays(30) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

if (-not $certificate) {
    Write-Host "Creazione del certificato locale di firma codice..." -ForegroundColor Cyan
    $certificate = New-SelfSignedCertificate -Subject $subject -FriendlyName $friendlyName `
        -Type CodeSigningCert -CertStoreLocation "Cert:\CurrentUser\My" -KeyAlgorithm RSA `
        -KeyLength 3072 -HashAlgorithm SHA256 -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(5)
}
New-Item -ItemType Directory -Force -Path $certificateFolder | Out-Null
Export-Certificate -Cert $certificate -FilePath $publicCertificate -Force | Out-Null
Write-Host "Certificato pronto: $publicCertificate" -ForegroundColor Green
Write-Host "Per renderlo attendibile, installare manualmente il file .cer negli archivi Autorita di certificazione radice attendibili ed Editori attendibili dell'utente corrente." -ForegroundColor Yellow
