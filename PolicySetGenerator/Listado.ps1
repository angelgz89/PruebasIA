# ==============================================================================
#   CONFIGURACION  -  edita estas variables antes de ejecutar el script
# ==============================================================================

$InputDir  = "C:\Users\angel.a.gutierrez\Git\Pruebas\output"
$OutputTxt = "C:\Users\angel.a.gutierrez\Git\Pruebas\policy_list.txt"

# ==============================================================================

$files = Get-ChildItem -Path $InputDir -Filter "*.json" | Where-Object { $_.Name -notlike "_*" }

if ($files.Count -eq 0) {
    Write-Host "  [ERR ]  No se encontraron ficheros JSON en: $InputDir" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  Leyendo $($files.Count) ficheros JSON..." -ForegroundColor Cyan

$lines = @()

foreach ($file in $files | Sort-Object Name) {
    try {
        $json = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $displayName = $json.displayName
        $id          = $json.id.Split('/')[-1]
        $lines += "$displayName`t$id"
    } catch {
        Write-Host "  [WARN]  No se pudo leer: $($file.Name)" -ForegroundColor Yellow
    }
}

$lines | Out-File -FilePath $OutputTxt -Encoding UTF8

Write-Host "  [ OK ]  Listado generado: $OutputTxt ($($lines.Count - 1) entradas)" -ForegroundColor Green
Write-Host ""