# scripts/backup_db.ps1
#
# Sauvegarde la base Postgres (identifiants lus depuis .env, quel que soit l'hôte
# visé — local ou distant) dans backups/, format personnalisé pg_dump (-Fc, compressé,
# restaurable avec pg_restore). Rétention : supprime les dumps plus vieux que
# $retentionDays. Prévu pour tourner régulièrement via le Planificateur de tâches
# Windows (voir scripts/register_backup_task.ps1).

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"
$backupDir = Join-Path $root "backups"
$retentionDays = 30

if (-not (Test-Path $envFile)) {
    Write-Error "Fichier .env introuvable : $envFile"
    exit 1
}

$envVars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    if ($_ -match '^([^=]+)=(.*)$') {
        $envVars[$matches[1].Trim()] = $matches[2].Trim()
    }
}

$dbHostName = $envVars["DB_HOST"]
$dbPort = $envVars["DB_PORT"]
$dbUser = $envVars["DB_USER"]
$dbPassword = $envVars["DB_PASSWORD"]
$dbName = $envVars["DB_NAME"]

if (-not $dbHostName -or -not $dbUser -or -not $dbName) {
    Write-Error "DB_HOST/DB_USER/DB_NAME manquants dans .env"
    exit 1
}

New-Item -ItemType Directory -Force -Path $backupDir | Out-Null

$pgDump = (Get-Command pg_dump -ErrorAction SilentlyContinue).Source
if (-not $pgDump) {
    # Fallback si le Planificateur de tâches n'hérite pas du PATH utilisateur (scoop).
    $pgDump = "$env:USERPROFILE\scoop\apps\postgresql\current\bin\pg_dump.exe"
}
if (-not (Test-Path $pgDump)) {
    Write-Error "pg_dump introuvable (ni dans PATH, ni à $pgDump)"
    exit 1
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $backupDir "a3_db_$timestamp.dump"

$env:PGPASSWORD = $dbPassword
try {
    & $pgDump -h $dbHostName -p $dbPort -U $dbUser -d $dbName -Fc -f $outFile
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump a échoué (code de sortie $LASTEXITCODE)"
    }
    $sizeMo = (Get-Item $outFile).Length / 1MB
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Backup OK : $outFile ($('{0:N2}' -f $sizeMo) Mo)"
} finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

# Rétention : supprime les dumps plus vieux que $retentionDays.
$limite = (Get-Date).AddDays(-$retentionDays)
Get-ChildItem -Path $backupDir -Filter "a3_db_*.dump" | Where-Object { $_.LastWriteTime -lt $limite } | ForEach-Object {
    Write-Output "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Suppression backup expiré : $($_.Name)"
    Remove-Item $_.FullName -Force
}
