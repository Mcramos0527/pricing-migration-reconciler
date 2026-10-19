# load and validate the YAML configuration

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import yaml

# Key columns that identify each access level. A record belongs to the most specific
# level whose key fields are filled (blank fields mean "any").
LEVEL_KEYS: dict[str, tuple[str, ...]] = {
    "customer_material": ("kunnr", "matnr"),
    "customer": ("kunnr",),
    "material": ("matnr",),
    "sales_org": (),
}

STEP_KINDS = {"price", "percent", "fixed", "subtotal"}
ROUNDING_MODES = {"per_step", "at_end"}
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    # one line of the flattened waterfall

    id: str
    label: str
    kind: str
    cond: str | None = None
    base: tuple[str, ...] = ()
    sign: int = 1
    sum: tuple[str, ...] = ()


@dataclass(frozen=True)
class WaterfallConfig:
    reference_quantity: int
    decimals: int
    procedure: tuple[Step, ...]
    access_sequences: dict[str, tuple[str, ...]]

    @property
    def price_step(self) -> Step:
        return self.procedure[0]

    @property
    def cond_types(self) -> tuple[str, ...]:
        return tuple(s.cond for s in self.procedure if s.cond)

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.procedure)

    def step_by_cond(self, cond: str) -> Step:
        for step in self.procedure:
            if step.cond == cond:
                return step
        raise KeyError(cond)


@dataclass(frozen=True)
class Settings:
    ecc_url_env: str
    s4_url_env: str
    tables: dict[str, str]
    tolerance_rounding: Decimal
    pricing_date_offsets_days: tuple[int, ...]
    rounding: dict[str, str]
    default_cutoff: str
    lock_stale_hours: float
    output_dir: Path
    excel_name: str
    summary_name: str
    write_empty_report: bool
    keep_days: int
    log_dir: Path
    log_file: str
    log_level: str
    log_max_bytes: int
    log_backup_count: int

    def database_url(self, which: str) -> str:
        # resolve a connection string from its environment variable
        env_name = self.ecc_url_env if which == "ecc" else self.s4_url_env
        value = os.environ.get(env_name)
        if not value:
            raise ConfigError(
                f"Environment variable {env_name} is not set "
                f"(it must hold the {which.upper()} database URL; see .env.example)."
            )
        return value


@dataclass(frozen=True)
class AppConfig:
    settings: Settings
    waterfall: WaterfallConfig
    causes: dict[str, dict[str, Any]]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file is empty or not a mapping: {path}")
    return data


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"Missing '{key}' in {where}")
    return mapping[key]


def _parse_step(raw: Mapping[str, Any]) -> Step:
    step_id = str(_require(raw, "id", "a procedure step"))
    kind = str(_require(raw, "kind", f"step '{step_id}'"))
    if kind not in STEP_KINDS:
        raise ConfigError(f"Step '{step_id}': kind '{kind}' must be one of {sorted(STEP_KINDS)}")
    cond = raw.get("cond")
    if kind != "subtotal" and not cond:
        raise ConfigError(f"Step '{step_id}': kind '{kind}' needs a 'cond' (condition type)")
    return Step(
        id=step_id,
        label=str(raw.get("label", step_id)),
        kind=kind,
        cond=str(cond) if cond else None,
        base=tuple(raw.get("base", ())),
        sign=int(raw.get("sign", 1)),
        sum=tuple(raw.get("sum", ())),
    )


def load_waterfall(path: Path) -> WaterfallConfig:
    raw = _read_yaml(path)
    steps = tuple(_parse_step(item) for item in _require(raw, "procedure", str(path)))
    if not steps or steps[0].kind != "price":
        raise ConfigError("The first procedure step must be of kind 'price' (the gross price).")

    seen: set[str] = set()
    for step in steps:
        if step.id in seen:
            raise ConfigError(f"Duplicate step id '{step.id}'")
        for ref in (*step.base, *step.sum):
            if ref not in seen:
                raise ConfigError(
                    f"Step '{step.id}' refers to '{ref}', which is not defined before it"
                )
        if step.kind == "subtotal" and not step.sum:
            raise ConfigError(f"Subtotal '{step.id}' needs a 'sum' list")
        if step.kind == "percent" and not step.base:
            raise ConfigError(f"Percent step '{step.id}' needs a 'base' list")
        seen.add(step.id)

    access = {
        str(cond): tuple(levels)
        for cond, levels in _require(raw, "access_sequences", str(path)).items()
    }
    for step in steps:
        if step.cond and step.cond not in access:
            raise ConfigError(f"Condition type '{step.cond}' has no access sequence")
    for cond, levels in access.items():
        for level in levels:
            if level not in LEVEL_KEYS:
                raise ConfigError(
                    f"Access sequence of '{cond}': unknown level '{level}' "
                    f"(valid: {sorted(LEVEL_KEYS)})"
                )

    return WaterfallConfig(
        reference_quantity=int(raw.get("reference_quantity", 1)),
        decimals=int(raw.get("decimals", 2)),
        procedure=steps,
        access_sequences=access,
    )


def load_settings(path: Path) -> Settings:
    raw = _read_yaml(path)
    databases = _require(raw, "databases", str(path))
    tables = {str(k): str(v) for k, v in _require(raw, "tables", str(path)).items()}
    for key in ("ecc_conditions", "ecc_scope", "s4_conditions", "map_customer", "map_material"):
        if key not in tables:
            raise ConfigError(f"Missing table name '{key}' in {path}")
    for name in tables.values():
        if not _IDENTIFIER.match(name):
            raise ConfigError(f"Invalid table name '{name}': use letters, digits and underscores")

    comparison = raw.get("comparison", {})
    rounding = {"ecc": "per_step", "s4": "per_step", **comparison.get("rounding", {})}
    for system, mode in rounding.items():
        if mode not in ROUNDING_MODES:
            raise ConfigError(f"Rounding mode for {system} must be one of {sorted(ROUNDING_MODES)}")

    tolerance = Decimal(str(comparison.get("tolerance_rounding", "0.01")))
    if tolerance < 0:
        raise ConfigError("tolerance_rounding cannot be negative")

    run = raw.get("run", {})
    default_cutoff = str(run.get("default_cutoff", "yesterday_end"))
    if default_cutoff not in {"yesterday_end", "now"}:
        raise ConfigError("run.default_cutoff must be 'yesterday_end' or 'now'")

    output = raw.get("output", {})
    log = raw.get("logging", {})
    return Settings(
        ecc_url_env=str(_require(databases["ecc"], "url_env", "databases.ecc")),
        s4_url_env=str(_require(databases["s4"], "url_env", "databases.s4")),
        tables=tables,
        tolerance_rounding=tolerance,
        pricing_date_offsets_days=tuple(
            int(x) for x in comparison.get("pricing_date_offsets_days", [0])
        ),
        rounding=rounding,
        default_cutoff=default_cutoff,
        lock_stale_hours=float(run.get("lock_stale_hours", 6)),
        output_dir=Path(output.get("directory", "output")),
        excel_name=str(output.get("excel_name", "pricing_mismatches_{cutoff:%Y%m%d}.xlsx")),
        summary_name=str(output.get("summary_name", "run_summary_{cutoff:%Y%m%d}.json")),
        write_empty_report=bool(output.get("write_empty_report", True)),
        keep_days=int(output.get("keep_days", 30)),
        log_dir=Path(log.get("directory", "logs")),
        log_file=str(log.get("file", "reconciler.log")),
        log_level=str(log.get("level", "INFO")).upper(),
        log_max_bytes=int(log.get("max_bytes", 5_000_000)),
        log_backup_count=int(log.get("backup_count", 10)),
    )


def load_config(config_dir: str | Path = "config") -> AppConfig:
    directory = Path(config_dir)
    causes_raw = _read_yaml(directory / "causes.yaml")
    rules = _require(causes_raw, "rules", "causes.yaml")
    return AppConfig(
        settings=load_settings(directory / "settings.yaml"),
        waterfall=load_waterfall(directory / "waterfall.yaml"),
        causes={str(k): dict(v) for k, v in rules.items()},
    )


def resolve_cutoff(
    value: str | None, mode: str = "yesterday_end", now: datetime | None = None
) -> datetime:
    now = now or datetime.now()
    if value:
        text = value.strip()
        try:
            if "T" in text or " " in text:
                return datetime.fromisoformat(text)
            return datetime.combine(date.fromisoformat(text), time(23, 59, 59))
        except ValueError as exc:
            raise ConfigError(
                f"Cannot read cutoff '{value}'. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
            ) from exc
    if mode == "now":
        return now.replace(microsecond=0)
    yesterday = now.date() - timedelta(days=1)
    return datetime.combine(yesterday, time(23, 59, 59))
