$InputFile         = "C:\Users\angel.a.gutierrez\Git\Pruebas\politicas.txt"   # Ruta al fichero con los displayNames
$OutputDir         = "C:\Users\angel.a.gutierrez\Git\Pruebas\output"                 # Carpeta donde se guardaran los JSON

$SubscriptionId    = "d37fb99d-56d0-49f7-8632-a31be42ea4c9"    # Opcional: ID de suscripcion. Vacio = suscripcion activa en az cli
$ManagementGroupId = "031a09bc-a2bf-44df-888e-4e09355b7a24"    # Opcional: ID de Management Group. Vacio = ambito de suscripcion

# ==============================================================================


# --- Helpers ------------------------------------------------------------------

function Write-Info ($msg) { Write-Host "  [INFO]  $msg" -ForegroundColor Cyan }
function Write-Ok   ($msg) { Write-Host "  [ OK ]  $msg" -ForegroundColor Green }
function Write-Warn ($msg) { Write-Host "  [WARN]  $msg" -ForegroundColor Yellow }
function Write-Err  ($msg) { Write-Host "  [ERR ]  $msg" -ForegroundColor Red }

function Get-SafeFileName ($name) {
    $safe = $name -replace '[\\/:*?"<>|]', '_'
    $safe = $safe.Trim()
    if ($safe.Length -gt 180) { $safe = $safe.Substring(0, 180) }
    return $safe
}

# --- Cabecera -----------------------------------------------------------------

Write-Host ""
Write-Host "==========================================================" -ForegroundColor DarkCyan
Write-Host "  Azure Policy to JSON   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor DarkCyan
Write-Host ""

# --- Validaciones -------------------------------------------------------------

if (-not (Get-Command "az" -ErrorAction SilentlyContinue)) {
    Write-Err "az cli no encontrado. Instalalo o anadelo al PATH."
    exit 1
}

az account show *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Err "No hay sesion activa. Ejecuta 'az login' primero."
    exit 1
}

if ($SubscriptionId -ne "") {
    az account set --subscription $SubscriptionId *>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "No se pudo establecer la suscripcion '$SubscriptionId'."
        exit 1
    }
    Write-Info "Suscripcion fijada : $SubscriptionId"
} else {
    $subName = (az account show --query "name" -o tsv 2>$null)
    Write-Info "Suscripcion activa : $subName"
}

if ($ManagementGroupId -ne "") {
    Write-Info "Ambito             : Management Group '$ManagementGroupId'"
} else {
    Write-Info "Ambito             : Suscripcion"
}

if (-not (Test-Path $InputFile)) {
    Write-Err "Fichero de entrada no encontrado: $InputFile"
    exit 1
}

$displayNames = Get-Content $InputFile -Encoding UTF8 |
    Where-Object { $_.Trim() -ne "" -and -not $_.TrimStart().StartsWith("#") } |
    ForEach-Object { $_.Trim() }

if ($displayNames.Count -eq 0) {
    Write-Err "El fichero no contiene entradas validas."
    exit 1
}

Write-Info "Fichero de entrada  : $InputFile"
Write-Info "Politicas a buscar  : $($displayNames.Count)"
Write-Host ""

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}
Write-Info "Directorio de salida: $(Resolve-Path $OutputDir)"
Write-Host ""

Write-Info "Descargando catalogo completo de Policy Definitions..."

$tmpFile = Join-Path $env:TEMP "az_policy_catalog_$([System.Guid]::NewGuid().ToString('N')).json"

try {
    if ($ManagementGroupId -ne "") {
        $azCmd = "az policy definition list --management-group `"$ManagementGroupId`" -o json"
    } else {
        $azCmd = "az policy definition list -o json"
    }

    cmd /c "$azCmd > `"$tmpFile`" 2>&1"

    if ($LASTEXITCODE -ne 0) {
        $errContent = Get-Content $tmpFile -Raw -ErrorAction SilentlyContinue
        Write-Err "az cli devolvio un error:"
        Write-Err $errContent
        exit 1
    }

    $catalogRaw = Get-Content $tmpFile -Raw -Encoding UTF8

    if (-not $catalogRaw -or $catalogRaw.Trim() -eq "") {
        Write-Err "La respuesta del catalogo esta vacia."
        exit 1
    }

    # az puede emitir lineas WARNING: antes del JSON — se eliminan antes de parsear
    $catalogClean = ($catalogRaw -split "`n" | Where-Object { $_ -notmatch '^\s*WARNING:' }) -join "`n"

    try {
        $catalog = $catalogClean | ConvertFrom-Json
    } catch {
        Write-Err "No se pudo parsear la respuesta JSON del catalogo."
        Write-Err "Error: $_"
        Write-Err "Primeros 300 chars: $($catalogClean.Substring(0, [Math]::Min(300, $catalogClean.Length)))"
        exit 1
    }
} finally {
    if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force }
}

Write-Ok "Catalogo cargado: $($catalog.Count) definiciones totales."
Write-Host ""

# --- Exportacion por displayName ----------------------------------------------

$ok     = 0
$fail   = 0
$errors = [System.Collections.Generic.List[PSCustomObject]]::new()

$i = 0
foreach ($displayName in $displayNames) {
    $i++
    Write-Host "  [$i/$($displayNames.Count)] " -NoNewline -ForegroundColor DarkGray
    Write-Host $displayName -ForegroundColor White

    # Normalizar guiones: en dash (U+2013) y em dash (U+2014) a guion simple ASCII
    $enDash = [char]0x2013
    $emDash = [char]0x2014
    $displayNameNorm = $displayName.Replace($enDash, '-').Replace($emDash, '-')

    $found = @($catalog | Where-Object {
        $catalogNorm = $_.displayName.Replace($enDash, '-').Replace($emDash, '-')
        $catalogNorm -ieq $displayNameNorm
    })

    if ($found.Count -eq 0) {
        Write-Err "  No encontrada en el catalogo."
        $errors.Add([PSCustomObject]@{ DisplayName = $displayName; Error = "No encontrada en el catalogo" })
        $fail++
        continue
    }

    # Si hay varias coincidencias, quedarse solo con las Custom (evita duplicar built-in vs custom)
    if ($found.Count -gt 1) {
        $custom = @($found | Where-Object { $_.policyType -ieq "Custom" })
        if ($custom.Count -gt 0) {
            $found = $custom
            if ($found.Count -eq 1) {
                Write-Warn "  Varias coincidencias: se usa la version Custom."
            } else {
                Write-Warn "  Varias coincidencias Custom ($($found.Count)). Se exportan todas con sufijo numerico."
            }
        } else {
            Write-Warn "  Varias coincidencias sin ninguna Custom ($($found.Count)). Se exportan todas con sufijo numerico."
        }
    }

    $idx = 1
    foreach ($policy in $found) {
        $safeBase = Get-SafeFileName $displayName
        $outName  = if ($found.Count -gt 1) { "${safeBase}_${idx}.json" } else { "${safeBase}.json" }
        $outPath  = Join-Path $OutputDir $outName

        $c = 1
        while (Test-Path $outPath) {
            $outPath = Join-Path $OutputDir "${safeBase}_dup${c}.json"
            $c++
        }

        $jsonContent = $policy | ConvertTo-Json -Depth 50
        # PowerShell escapa caracteres Unicode por defecto (\u0027 en vez de ')
        # Se decodifican de vuelta a caracteres legibles
        $jsonContent = [System.Text.RegularExpressions.Regex]::Unescape($jsonContent)
        $jsonContent | Out-File -FilePath $outPath -Encoding UTF8
        Write-Ok "  -> $(Split-Path $outPath -Leaf)"
        $idx++
        $ok++
    }
}

# --- Resumen final ------------------------------------------------------------

Write-Host ""
Write-Host "==========================================================" -ForegroundColor DarkCyan
Write-Host "   RESUMEN" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor DarkCyan
Write-Host "  Exportadas correctamente : " -NoNewline; Write-Host $ok   -ForegroundColor Green
Write-Host "  No encontradas / errores : " -NoNewline; Write-Host $fail -ForegroundColor Red
Write-Host "  Directorio de salida     : $(Resolve-Path $OutputDir)" -ForegroundColor Cyan

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "  Politicas no encontradas:" -ForegroundColor Red
    foreach ($e in $errors) {
        Write-Host "    - $($e.DisplayName)" -ForegroundColor DarkRed
    }

    $logPath = Join-Path $OutputDir "_not_found.log"
    $errors | ForEach-Object { $_.DisplayName } | Out-File -FilePath $logPath -Encoding UTF8
    Write-Host ""
    Write-Host "  Log guardado en: $logPath" -ForegroundColor DarkYellow
}

Write-Host ""