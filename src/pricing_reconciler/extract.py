# read the data snapshot from both databases

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import DateTime, Engine, bindparam, text

from .config import Settings

log = logging.getLogger(__name__)

_CONDITION_SQL = (
    "SELECT kschl, vkorg, kunnr, matnr, datab, datbi, kbetr, konwa, kpein, kmein, scale_qty "
    "FROM {table} WHERE changed_on <= :cutoff"
)


@dataclass
class ExtractedData:
    ecc_records: pd.DataFrame
    s4_records: pd.DataFrame
    scope: pd.DataFrame
    map_customer: pd.DataFrame
    map_material: pd.DataFrame


def yyyymmdd(value: Any) -> int:
    if isinstance(value, (date, datetime)):
        return value.year * 10000 + value.month * 100 + value.day
    return int(str(value)[:10].replace("-", ""))


def _read(engine: Engine, sql: str, cutoff: datetime | None = None) -> pd.DataFrame:
    statement = text(sql)
    if cutoff is not None:
        statement = statement.bindparams(bindparam("cutoff", value=cutoff, type_=DateTime))
    with engine.connect() as connection:
        result = connection.execute(statement)
        return pd.DataFrame(result.fetchall(), columns=list(result.keys()))


def _clean_text(series: pd.Series) -> pd.Series:
    # SQL Server CHAR columns are right-padded with spaces; NULL becomes "" (an SAP blank).
    return series.fillna("").astype(str).str.strip()


def normalise_conditions(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in ("kschl", "vkorg", "kunnr", "matnr", "konwa", "kmein"):
        out[column] = _clean_text(out[column])
    out["datab"] = pd.Series([yyyymmdd(v) for v in out["datab"]], index=out.index, dtype="int64")
    out["datbi"] = pd.Series([yyyymmdd(v) for v in out["datbi"]], index=out.index, dtype="int64")
    out["kbetr"] = pd.Series([Decimal(str(v)) for v in out["kbetr"]], index=out.index, dtype=object)
    out["kpein"] = out["kpein"].astype("int64")
    out["scale_qty"] = out["scale_qty"].astype("int64")

    has_customer = (out["kunnr"] != "").to_numpy()
    has_material = (out["matnr"] != "").to_numpy()
    out["level"] = np.select(
        [has_customer & has_material, has_customer, has_material],
        ["customer_material", "customer", "material"],
        default="sales_org",
    )
    return out.reset_index(drop=True)


def extract_all(
    ecc_engine: Engine, s4_engine: Engine, settings: Settings, cutoff: datetime
) -> ExtractedData:
    tables = settings.tables
    ecc = normalise_conditions(
        _read(ecc_engine, _CONDITION_SQL.format(table=tables["ecc_conditions"]), cutoff)
    )
    s4 = normalise_conditions(
        _read(s4_engine, _CONDITION_SQL.format(table=tables["s4_conditions"]), cutoff)
    )

    scope = _read(ecc_engine, f"SELECT vkorg, kunnr, matnr FROM {tables['ecc_scope']}")
    for column in scope.columns:
        scope[column] = _clean_text(scope[column])

    map_customer = _read(s4_engine, f"SELECT kunnr_ecc, partner_s4 FROM {tables['map_customer']}")
    map_material = _read(s4_engine, f"SELECT matnr_ecc, matnr_s4 FROM {tables['map_material']}")
    for frame in (map_customer, map_material):
        for column in frame.columns:
            frame[column] = _clean_text(frame[column])

    log.info(
        "Extracted at cutoff %s: ECC records=%d, S/4 records=%d, scope rows=%d, "
        "customer mappings=%d, material mappings=%d",
        cutoff.isoformat(timespec="seconds"),
        len(ecc),
        len(s4),
        len(scope),
        len(map_customer),
        len(map_material),
    )
    return ExtractedData(ecc, s4, scope, map_customer, map_material)
