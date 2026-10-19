# pricing-migration-reconciler

[![CI](https://github.com/Mcramos0527/pricing-migration-reconciler/actions/workflows/ci.yml/badge.svg)](https://github.com/Mcramos0527/pricing-migration-reconciler/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

After a pricing migration from SAP ECC 4.7 to S/4HANA, which customer/material prices changed, and why?

This tool reads both databases, calculates the pricing waterfall on each side, and writes **one Excel
file with only the rows that do not match perfectly**, each with a probable cause and a suggested
action. It is built to run unattended every morning.

## About this repo

I built a tool like this in a previous role and ran it daily on a VM. I did not keep the code, so this is a rebuild from scratch.

- **Synthetic data only.** Nothing here comes from an employer or a client. The `seed` command
  generates the databases, with known differences planted in them.
- **Built with AI assistance (Claude).** The design comes from how I ran the original: the waterfall,
  the key mapping, the cut-off, the cause rules and the scheduling. AI wrote much of the code and the
  tests. I can walk through any module and change it live, and I am happy to do that on a call.
- **The commit history is short and recent** because it is a rebuild. It says nothing about how the
  original was developed.

## What you get

For each row that is not a perfect match, the Excel shows the ECC value, the S/4HANA value and the
difference for every step of the waterfall, the condition records each system actually used, and the
cause with its evidence. Three real rows from the demo:

| Customer | Material | Status | Net ECC | Net S/4 | Δ | Cause |
|---|---|---|---:|---:|---:|---|
| 2 | M00000133 | MISMATCH | 262.20 | 297.96 | 35.76 | Condition record found in ECC but missing in S/4HANA |
| 6 | M00000192 | MISMATCH | 259.17 | 259.17 | 0.00 | Condition currency differs between ECC and S/4HANA |
| 104 | M00000054 | ROUNDING | 316.97 | 316.98 | 0.01 | Difference within the rounding tolerance |

Look at the second row: the net value is identical, but the price condition is in EUR in ECC and
USD in S/4HANA. A check that only compares amounts would miss it. The evidence column says:

> `[R02] PR00: ECC customer_material 258.01 EUR/1 EA, valid 2026-01-19 to 9999-12-31 | S/4
> customer_material 258.01 USD/1 EA, valid 2026-01-19 to 9999-12-31`

## Run it (about a minute)

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
pricing-reconciler seed --as-of 2026-10-18
pricing-reconciler run --cutoff 2026-10-18
pytest
```

**Linux / macOS**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pricing-reconciler seed --as-of 2026-10-18 && pricing-reconciler run --cutoff 2026-10-18 && pytest
```

You should see: **3,000 rows compared, 2,934 perfect, 6 rounding, 60 mismatch.** Those 66 are exactly
the differences the seed planted (answer key in `demo/planted_differences.csv`). A test checks that
the tool finds every one of them with the expected cause and flags nothing else.

One caveat: the generator and the tool share the same assumptions, so this shows the logic is
consistent, not that it is correct for your system. On real data expect to adapt the config and
review the causes.

## Try to break it

These use the demo data and change one setting each. The results are what I get when I run them.

- **Rounding strategy.** Set `rounding.s4` to `at_end` in `config/settings.yaml`. Rounding rows go
  from 6 to 656, because S/4HANA now rounds once at the end instead of at every step. Where each
  system rounds is something to confirm on the real systems, which is why it is a setting.
- **Tolerance.** Set `tolerance_rounding` to `"0.00"`. With the setting above, the 656 rounding rows
  become mismatches (716 in total), so the tolerance is what separates noise from errors.
- **Cut-off.** `pricing-reconciler run --cutoff 2026-10-10` uses only records changed on or before
  that date in both systems, so a change made later cannot create a false difference.

## How it works

1. **Extract** both systems with `changed_on <= cutoff` applied in the SQL.
2. **Map** ECC keys to S/4HANA keys with mapping tables (customer to business partner, 18-character
   to 40-character material).
3. **Resolve** the condition records per row with an access sequence (most specific key first,
   validity dates, scale tier, latest start wins). Sequences are defined in YAML.
4. **Calculate** the waterfall in `Decimal` on both sides and flatten it to one row.
5. **Compare** exactly. A difference of at most 0.01 on every step is `ROUNDING`. Anything else, or a
   currency or unit difference, a price on one side only, or an unmapped key, is `MISMATCH`.
6. **Explain** each row with a fixed decision tree (R01 to R10, in
   [docs/causes_reference.md](docs/causes_reference.md)). No machine learning, so the same facts
   always give the same answer and each cause can be audited.
7. **Report** to Excel (one sheet, `Mismatches`), a JSON summary and a rotating log.

A few choices worth knowing:

- `Decimal` with `ROUND_HALF_UP`, never float: `2.675` must become `2.68`, and a float gives `2.67`.
- Dates travel as `yyyymmdd` integers because `9999-12-31` overflows pandas `datetime64[ns]`.
- Key mapping lives in tables, not code, because conversion rules change during a migration.
- For unattended use: a lock file with stale detection, an atomic Excel write, retention cleanup, and
  exit codes (`0` ok, `1` failure, `2` differences found with `--fail-on-mismatch`, `3` already running).
- Credentials come only from environment variables or a git-ignored `.env`. Logs show host and
  database name only.

## Running it every morning (Windows VM)

```powershell
.\scripts\register_task.ps1 -At "05:00"
```

This registers a Task Scheduler job for `scripts\run_daily.ps1`, which uses the end of the previous
day as the cut-off. For Windows authentication to a remote SQL Server, see
[docs/scheduling_windows.md](docs/scheduling_windows.md).

## Configuration

- `config/settings.yaml`: database variable names, tables, tolerance, rounding per system, output
- `config/waterfall.yaml`: the waterfall steps and the access sequence of each condition type
- `config/causes.yaml`: wording, confidence and action for each cause

## Known gaps

This is a simplified model, and it differs from real SAP in places:

- **Procedure.** Condition types are illustrative and `ZREB` is an invented custom type. A real
  pricing procedure needs its own `waterfall.yaml`.
- **Rebates.** Here a rebate is a condition record in both systems. In S/4HANA, rebate agreements
  move to Settlement Management, so a real migration compares them differently.
- **Tax.** `MWST` is resolved by customer or sales org. Real tax determination uses country and tax
  classification.
- **Scales.** The tier is chosen at one fixed reference quantity. SAP uses the order quantity.
- **Not modelled:** condition exclusion, group and header conditions, custom requirements and
  formulas, several currencies in one document.
- **Tested on SQLite only.** The SQL Server connection, Docker setup, PowerShell scripts and CI follow
  standard patterns, but the tests do not run them against real servers.
- **Ideas:** Teams or e-mail alert, a trend of causes over time, a summary sheet.

## Author

Max Ramos, Dublin. Portfolio: https://mcramos0527.github.io/maxramos

MIT license, see [LICENSE](LICENSE).