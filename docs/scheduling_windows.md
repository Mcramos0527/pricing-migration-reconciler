# Scheduling on a Windows VM

## One-time setup

```powershell
git clone https://github.com/Mcramos0527/pricing-migration-reconciler.git C:\apps\pricing-reconciler
cd C:\apps\pricing-reconciler
python -m venv .venv
.\.venv\Scripts\pip install -e ".[mssql]"
copy .env.example .env       # then edit the two RECON_* URLs
.\.venv\Scripts\pricing-reconciler check
.\scripts\register_task.ps1 -At "05:00"
```

Requires Microsoft ODBC Driver 18 for SQL Server on the VM.

## Which account runs the task

- **SQL login** in the URLs: simplest. The task can run as any account; keep `.env` readable only
  by it.
- **Windows authentication** to a remote SQL Server: the task must run with a stored password
  (`-Credential`) or as a group managed service account (`-GroupManagedServiceAccount`). A task
  that is only "run whether user is logged on or not" without a stored password cannot reach
  network resources.

## Operating it

| Need | Command |
|---|---|
| Run now | `Start-ScheduledTask -TaskName PricingReconciler-Daily` |
| Last result | `Get-ScheduledTaskInfo -TaskName PricingReconciler-Daily` |
| Re-run a past day | `.\scripts\run_daily.ps1 -Cutoff 2026-10-17` |
| Logs | `logs\reconciler.log` (rotating) |
| Output | `output\pricing_mismatches_YYYYMMDD.xlsx` and `run_summary_YYYYMMDD.json` |

Exit codes: 0 ok, 1 failure, 2 differences found (only with `-FailOnMismatch`), 3 another run active.

If the VM was off at 05:00 the task runs when it is back (`StartWhenAvailable`).
