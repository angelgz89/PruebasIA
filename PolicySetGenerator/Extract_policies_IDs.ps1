$InputFile  = ".\politicas.txt"
$OutputFile = ".\ids.txt"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "Cargando policy definitions desde Azure..." -ForegroundColor Cyan
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:AZURE_CORE_COLLECT_TELEMETRY = "0"
# Escribir el JSON a un fichero temporal para evitar problemas de encoding en la consola
$tmpFile = [System.IO.Path]::GetTempFileName() + ".json"
az policy definition list --output json | Out-File -FilePath $tmpFile -Encoding UTF8
$allPolicies = Get-Content $tmpFile -Encoding UTF8 -Raw | ConvertFrom-Json
Remove-Item $tmpFile -Force

$results  = @()
$notFound = @()

foreach ($line in Get-Content $InputFile -Encoding UTF8) {
    $line = $line.Trim()
    if ([string]::IsNullOrEmpty($line)) { continue }

    $match = $allPolicies | Where-Object {
        $_.displayName -eq $line -or $_.name -eq $line
    } | Select-Object -First 1

    if ($match) {
        Write-Host "  OK  $($match.displayName)" -ForegroundColor Green
        $results += [PSCustomObject]@{
            Name = $match.displayName
            ID   = $match.id
        }
    } else {
        Write-Host "  --  $line (no encontrada)" -ForegroundColor Yellow
        $notFound += $line
    }
}

$results | ForEach-Object { "$($_.Name)`t$($_.ID.Split('/')[-1])" } | Set-Content $OutputFile -Encoding UTF8

Write-Host ""
Write-Host "Guardado en: $OutputFile ($($results.Count) policies)" -ForegroundColor Cyan

if ($notFound.Count -gt 0) {
    Write-Host "No encontradas ($($notFound.Count)): $($notFound -join ', ')" -ForegroundColor Red
}