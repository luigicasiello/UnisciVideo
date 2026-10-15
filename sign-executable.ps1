param([Parameter(Mandatory = $true)][string]$Path)
$ErrorActionPreference = "Stop"
$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
$certificate = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq "CN=UnisciVideo Local Publisher" -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if (-not $certificate) { throw "Certificato di firma non trovato. Eseguire setup-local-signing.ps1." }
$signature = Set-AuthenticodeSignature -LiteralPath $resolvedPath -Certificate $certificate -HashAlgorithm SHA256 -IncludeChain All
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Firma non valida: $($signature.Status) - $($signature.StatusMessage)"
}
Write-Host "Firma valida: $resolvedPath" -ForegroundColor Green
