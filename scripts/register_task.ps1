<#
.SYNOPSIS
    Registers the daily 05:00 reconciliation in Windows Task Scheduler.

.DESCRIPTION
    Run once, from an elevated PowerShell, on the VM that will host the job.

    Authentication note: if the databases use Windows authentication on another server, the task
    must run as an account that can reach them over the network (a service account or a gMSA).
    Pass -Credential for a normal account, or use a SQL login in the .env file instead.

.EXAMPLE
    .\register_task.ps1
    .\register_task.ps1 -At "05:00" -Credential (Get-Credential DOMAIN\svc-pricing-recon)
    .\register_task.ps1 -GroupManagedServiceAccount "DOMAIN\gmsa-pricing$"
#>
param(
    [string]$TaskName = "PricingReconciler-Daily",
    [string]$At = "05:00",
    [System.Management.Automation.PSCredential]$Credential,
    [string]$GroupManagedServiceAccount = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot "run_daily.ps1"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" `
    -WorkingDirectory $RepoRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $At

# StartWhenAvailable: if the VM was off at 05:00, run as soon as it is back.
# IgnoreNew: never start a second copy while one is running (the tool also has its own lock).
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

$Description = "Pricing migration reconciler: ECC 4.7 vs S/4HANA waterfall comparison"

if ($GroupManagedServiceAccount) {
    $Principal = New-ScheduledTaskPrincipal -UserId $GroupManagedServiceAccount -LogonType Password
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Principal $Principal -Description $Description -Force | Out-Null
}
elseif ($Credential) {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -User $Credential.UserName `
        -Password $Credential.GetNetworkCredential().Password `
        -Description $Description -Force | Out-Null
}
else {
    Write-Warning "No credential given: the task is registered for the current user and only runs while that user is logged on."
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
        -Settings $Settings -Description $Description -Force | Out-Null
}

Write-Host "Task '$TaskName' registered to run daily at $At."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check the result:  Get-ScheduledTaskInfo -TaskName '$TaskName' | Select LastRunTime, LastTaskResult"
