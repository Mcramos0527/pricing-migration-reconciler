# command line: pricing-reconciler seed | check | run

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import text

from . import __version__
from .config import AppConfig, ConfigError, load_config, resolve_cutoff
from .db import check_connection, make_engine
from .lock import LockBusyError
from .logging_setup import configure_logging
from .runner import run_once
from .synthetic.generator import generate, seed_databases

log = logging.getLogger("pricing_reconciler")


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _ensure_sqlite_folder(url: str) -> None:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pricing-reconciler",
        description="Reconcile flattened pricing waterfalls between SAP ECC 4.7 and S/4HANA.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config-dir", default="config", help="folder with the YAML files")
    parser.add_argument("--env-file", default=".env", help="optional file with RECON_* variables")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", help="run the reconciliation once (this is what the scheduler calls)"
    )
    run.add_argument(
        "--cutoff", help="data cut-off: YYYY-MM-DD (end of day) or YYYY-MM-DDTHH:MM:SS"
    )
    run.add_argument(
        "--fail-on-mismatch", action="store_true", help="exit with code 2 if anything differs"
    )

    sub.add_parser("check", help="validate the configuration and test both database connections")

    seed = sub.add_parser(
        "seed", help="create the synthetic demo databases (drops existing tables)"
    )
    seed.add_argument("--as-of", help="date of the data cut for the demo data (default: yesterday)")
    seed.add_argument("--customers", type=int, default=300)
    seed.add_argument("--materials", type=int, default=200)
    seed.add_argument("--scope", type=int, default=3000, help="number of customer/material rows")
    seed.add_argument("--seed", type=int, default=42, help="random seed (same seed, same data)")
    seed.add_argument(
        "--answer-key",
        default="demo/planted_differences.csv",
        help="where to write the list of planted differences",
    )
    return parser


def _cmd_run(config: AppConfig, args: argparse.Namespace) -> int:
    cutoff = resolve_cutoff(args.cutoff, config.settings.default_cutoff)
    result = run_once(config, cutoff)
    print(
        f"Cutoff {result.cutoff}: compared {result.rows_compared}, perfect {result.perfect}, "
        f"rounding {result.rounding}, mismatch {result.mismatch}"
    )
    if result.excel_path:
        print(f"Report: {result.excel_path}")
    if args.fail_on_mismatch and result.not_perfect:
        return 2
    return 0


def _cmd_check(config: AppConfig) -> int:
    settings = config.settings
    for label, which in (("ECC", "ecc"), ("S/4HANA", "s4")):
        engine = make_engine(settings.database_url(which))
        check_connection(engine, label)
        tables = (
            [settings.tables["ecc_conditions"], settings.tables["ecc_scope"]]
            if which == "ecc"
            else [
                settings.tables["s4_conditions"],
                settings.tables["map_customer"],
                settings.tables["map_material"],
            ]
        )
        with engine.connect() as connection:
            for table in tables:
                count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"{label}: {table} -> {count} rows")
        engine.dispose()
    print("Configuration and connections OK.")
    return 0


def _cmd_seed(config: AppConfig, args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else resolve_cutoff(None).date()
    ecc_url, s4_url = config.settings.database_url("ecc"), config.settings.database_url("s4")
    for url in (ecc_url, s4_url):
        _ensure_sqlite_folder(url)
    dataset = generate(
        as_of,
        n_customers=args.customers,
        n_materials=args.materials,
        n_scope=args.scope,
        seed=args.seed,
    )
    ecc_engine, s4_engine = make_engine(ecc_url), make_engine(s4_url)
    seed_databases(dataset, ecc_engine, s4_engine, config.settings.tables)
    ecc_engine.dispose()
    s4_engine.dispose()

    key_path = Path(args.answer_key)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.planted.to_csv(key_path, index=False)
    flagged = int(dataset.planted["expect_flagged"].sum())
    print(
        f"Seeded synthetic data as of {as_of}: ECC conditions={len(dataset.ecc_conditions)}, "
        f"S/4 conditions={len(dataset.s4_conditions)}, scope rows={len(dataset.ecc_scope)}."
    )
    print(f"{flagged} rows have a planted difference. Answer key: {key_path}")
    print(f"Run it with:  pricing-reconciler run --cutoff {as_of}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    load_env_file(Path(args.env_file))
    try:
        config = load_config(args.config_dir)
        configure_logging(config.settings, console=args.command != "run" or True)
        if args.command == "run":
            return _cmd_run(config, args)
        if args.command == "check":
            return _cmd_check(config)
        return _cmd_seed(config, args)
    except LockBusyError as exc:
        log.error("%s", exc)
        return 3
    except (ConfigError, ConnectionError) as exc:
        log.error("%s", exc)
        return 1
    except Exception:  # noqa: BLE001
        log.exception("Unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
