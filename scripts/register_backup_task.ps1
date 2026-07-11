# scripts/register_backup_task.ps1
#
# Enregistre (ou met a jour) la tache planifiee Windows qui execute backup_db.ps1
# tous les jours a 04:00. A relancer si le chemin du projet change.

$ErrorActionPreference = "Stop"

$taskName = "A3-DB-Backup"
$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) "scripts\backup_db.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""

$trigger = New-ScheduledTaskTrigger -Daily -At 4:00AM

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Backup quotidien de la base A3 (pg_dump) - voir scripts/backup_db.ps1" -Force

Write-Output "Tache planifiee $taskName enregistree : tous les jours a 04:00 (rattrapage automatique si la machine est eteinte a cette heure)."
