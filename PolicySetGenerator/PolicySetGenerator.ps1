<#
.SYNOPSIS
    Genera el JSON de una Policy Initiative de Azure.

.DESCRIPTION
    - Lee un fichero TSV con formato:  displayName <TAB> UUID
      (generado con: az policy definition list --management-group <mgId>
                         --query '[?policyType==`Custom`].[displayName,name]' -o tsv)
    - Pide interactivamente el nombre, displayName y descripcion de la iniciativa.
    - Consulta los parametros de cada policy via az cli.
    - Soporta políticas Custom (bajo Management Group) y Built-in (globales) de forma automatica.
    - Genera el JSON completo listo para:
        az policy set-definition create --definitions initiative.json ...

.PARAMETER PolicyListFile
    Ruta al fichero TSV (displayName <TAB> UUID). Por defecto: ids.txt

.PARAMETER OutputFile
    Ruta del JSON de salida. Por defecto: initiative.json

.EXAMPLE
    .\New-PolicyInitiative.ps1
    .\New-PolicyInitiative.ps1 -PolicyListFile ".\mis_policies.txt" -OutputFile ".\mi_iniciativa.json"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$PolicyListFile = "ids.txt",

    [Parameter(Mandatory = $false)]
    [string]$OutputFile = "initiative.json"
)

Set-StrictMode -Version Latest
# IMPORTANTE: Continue en vez de Stop para que los errores de az CLI no corten el flujo
$ErrorActionPreference = "Continue"

# Forzar UTF-8 en la consola para mostrar tildes correctamente
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ManagementGroupId = "031a09bc-a2bf-44df-888e-4e09355b7a24"

function Write-Step($msg) { Write-Host "`n[*] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "    [+] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "    [x] $msg" -ForegroundColor Red }

# Llama a az policy definition show y devuelve el objeto parseado, o $null si no se encuentra
function Get-PolicyDefinition([string]$uuid, [string]$mgId) {
    # Intento 1: Custom bajo Management Group
    $raw = az policy definition show `
        --name $uuid `
        --management-group $mgId `
        --output json 2>&1

    $rawStr = $raw -join ''
    $isError = ($LASTEXITCODE -ne 0) -or ($rawStr -match 'ERROR:')

    if (-not $isError) {
        $jsonOnly = ($raw | Where-Object { $_ -notmatch '^(WARNING|ERROR):' }) -join ''
        try {
            $def = $jsonOnly | ConvertFrom-Json
            return @{ Def = $def; Source = "Custom" }
        } catch {
            # JSON invalido, seguir al fallback
        }
    }

    # Intento 2: Built-in global (sin --management-group)
    $raw = az policy definition show `
        --name $uuid `
        --output json 2>&1

    $rawStr = $raw -join ''
    $isError = ($LASTEXITCODE -ne 0) -or ($rawStr -match 'ERROR:')

    if (-not $isError) {
        $jsonOnly = ($raw | Where-Object { $_ -notmatch '^(WARNING|ERROR):' }) -join ''
        try {
            $def = $jsonOnly | ConvertFrom-Json
            return @{ Def = $def; Source = "BuiltIn" }
        } catch {
            # JSON invalido
        }
    }

    return $null
}

# ---------------------------------------------
# VALIDACIONES
# ---------------------------------------------

Write-Host ""
Write-Host "+==============================================+" -ForegroundColor Magenta
Write-Host "|      Azure Policy Initiative Generator       |" -ForegroundColor Magenta
Write-Host "+==============================================+" -ForegroundColor Magenta

Write-Step "Validando entorno..."

if (-not (Test-Path $PolicyListFile)) {
    Write-Fail "No se encuentra el fichero: $PolicyListFile"
    exit 1
}

$accountInfo = az account show 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "No hay sesion activa en Azure CLI. Ejecuta 'az login' primero."
    exit 1
}

$accountObj = ($accountInfo | Where-Object { $_ -notmatch '^(WARNING|ERROR):' }) -join '' | ConvertFrom-Json
Write-OK "Sesion activa : $($accountObj.user.name)"
Write-OK "Subscription  : $($accountObj.name)"

# ---------------------------------------------
# DATOS DE LA INICIATIVA (interactivo)
# ---------------------------------------------

Write-Step "Datos de la iniciativa..."
Write-Host ""

do {
    $InitiativeName = (Read-Host "  Nombre interno (sin espacios, ej: iberdrola-security-baseline)").Trim()
} while ($InitiativeName -eq '')

do {
    $InitiativeDisplayName = (Read-Host "  DisplayName (visible en el portal)").Trim()
} while ($InitiativeDisplayName -eq '')

$InitiativeDescription = (Read-Host "  Descripcion (opcional, Enter para dejar vacio)").Trim()

Write-Host ""
Write-OK "Nombre      : $InitiativeName"
Write-OK "DisplayName : $InitiativeDisplayName"
Write-OK "Descripcion : $(if ($InitiativeDescription) { $InitiativeDescription } else { '(vacia)' })"

# ---------------------------------------------
# LECTURA DEL FICHERO TSV
# ---------------------------------------------

Write-Step "Leyendo policies desde '$PolicyListFile'..."

$inputLines = Get-Content $PolicyListFile |
    Where-Object { $_.Trim() -ne '' -and -not $_.TrimStart().StartsWith('#') }

# Parsear cada linea: displayName <TAB> UUID
$policies = [System.Collections.Generic.List[hashtable]]::new()
foreach ($line in $inputLines) {
    $parts = $line -split "`t"
    if ($parts.Count -lt 2) {
        Write-Warn "Linea ignorada (no tiene tabulador): '$line'"
        continue
    }
    $policies.Add(@{
        DisplayName = $parts[0].Trim()
        UUID        = $parts[1].Trim()
    })
}

Write-OK "Policies a procesar: $($policies.Count)"

# ---------------------------------------------
# PROCESAMIENTO
# ---------------------------------------------

Write-Step "Consultando parametros y construyendo iniciativa..."

$policyDefinitions    = [System.Collections.Generic.List[object]]::new()
$initiativeParameters = [ordered]@{}
$failed               = [System.Collections.Generic.List[string]]::new()
$refIdCounter         = @{}

$counter = 0
foreach ($policy in $policies) {
    $displayName = $policy.DisplayName
    $uuid        = $policy.UUID

    Write-Host "    --> $displayName" -ForegroundColor DarkGray

    # Consultar definicion (Custom primero, Built-in como fallback)
    $result = Get-PolicyDefinition -uuid $uuid -mgId $ManagementGroupId

    if ($null -eq $result) {
        Write-Warn "No encontrada '$displayName' (UUID: $uuid) ni como Custom ni como Built-in. Se omite."
        $failed.Add($displayName)
        continue
    }

    $def          = $result.Def
    $policySource = $result.Source

    # Usar el id real devuelto por az (correcto para Custom y Built-in)
    $policyId = $def.id

    # Generar referenceId unico usando el displayName original con espacios
    $baseRefId = $displayName
    if ($refIdCounter.ContainsKey($baseRefId)) {
        $refIdCounter[$baseRefId]++
        $refId = "$baseRefId $($refIdCounter[$baseRefId])"
    } else {
        $refIdCounter[$baseRefId] = 0
        $refId = $baseRefId
    }

    # Procesar parametros
    $parameterValues = [ordered]@{}

    if ($def.PSObject.Properties['parameters'] -and $def.parameters) {
        $paramNames = @($def.parameters | Get-Member -MemberType NoteProperty -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
        foreach ($paramName in $paramNames) {
            $paramDef            = $def.parameters.$paramName
            # Separador " - " entre el nombre de la policy y el nombre del parametro
            $initiativeParamName = "$refId - $paramName"

            $initiativeParam = [ordered]@{ type = $paramDef.type }
            if ($paramDef.PSObject.Properties['metadata']      -and $paramDef.metadata)               { $initiativeParam.metadata      = $paramDef.metadata }
            if ($paramDef.PSObject.Properties['defaultValue']  -and $null -ne $paramDef.defaultValue) { $initiativeParam.defaultValue  = $paramDef.defaultValue }
            if ($paramDef.PSObject.Properties['allowedValues'] -and $paramDef.allowedValues)          { $initiativeParam.allowedValues = $paramDef.allowedValues }

            $initiativeParameters[$initiativeParamName] = $initiativeParam
            $parameterValues[$paramName] = [ordered]@{
                value = "[parameters('$initiativeParamName')]"
            }
        }
    }

    # Bloque policyDefinition
    $policyDefBlock = [ordered]@{
        policyDefinitionId          = $policyId
        policyDefinitionReferenceId = $refId
    }
    if ($parameterValues.Count -gt 0) {
        $policyDefBlock.parameters = $parameterValues
    }

    $policyDefinitions.Add($policyDefBlock)
    $counter++
    Write-OK "[$counter/$($policies.Count)] $displayName | tipo: $policySource | params: $($parameterValues.Count)"
}

# ---------------------------------------------
# ENSAMBLADO DEL JSON
# ---------------------------------------------

Write-Step "Generando JSON..."

$initiative = [ordered]@{
    name        = $InitiativeName
    displayName = $InitiativeDisplayName
    description = $InitiativeDescription
    metadata    = [ordered]@{
        category = "Security"
        version  = "1.0.0"
    }
    policyType  = "Custom"
}

if ($initiativeParameters.Count -gt 0) {
    $initiative.parameters = $initiativeParameters
}

$initiative.policyDefinitions = $policyDefinitions.ToArray()

$json = $initiative | ConvertTo-Json -Depth 20
# ConvertTo-Json escapa las comillas simples como \u0027 - revertirlo
$json = $json.Replace("\u0027", "'")
$json | Out-File -FilePath $OutputFile -Encoding UTF8 -Force

Write-OK "Fichero generado: $(Resolve-Path $OutputFile)"

# ---------------------------------------------
# RESUMEN
# ---------------------------------------------

Write-Host ""
Write-Host "+==============================================+" -ForegroundColor Magenta
Write-Host "|                  RESUMEN                     |" -ForegroundColor Magenta
Write-Host "+==============================================+" -ForegroundColor Magenta
Write-Host "  Policies incluidas  : $($policyDefinitions.Count)" -ForegroundColor White
Write-Host "  Parametros totales  : $($initiativeParameters.Count)" -ForegroundColor White
Write-Host "  Errores/omitidas    : $($failed.Count)" -ForegroundColor $(if ($failed.Count -gt 0) { "Yellow" } else { "White" })

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "  Policies con error:" -ForegroundColor Yellow
    foreach ($f in $failed) { Write-Host "    - $f" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "  Fichero de salida   : $OutputFile" -ForegroundColor White
Write-Host ""