
from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from .config import LEVEL_KEYS, WaterfallConfig
from .extract import yyyymmdd

_PAYLOAD = ["kbetr", "konwa", "kpein", "kmein", "scale_qty", "datab", "datbi"]
_RESOLVED_COLUMNS = ["row_id", "kschl", "found", "level", *_PAYLOAD]


def build_grain(
    scope: pd.DataFrame,
    map_customer: pd.DataFrame,
    map_material: pd.DataFrame,
    pricing_dates: Sequence[tuple[int, date]],
) -> pd.DataFrame:
    customers = dict(zip(map_customer["kunnr_ecc"], map_customer["partner_s4"]))
    materials = dict(zip(map_material["matnr_ecc"], map_material["matnr_s4"]))

    base = (
        scope.rename(columns={"kunnr": "kunnr_ecc", "matnr": "matnr_ecc"})
        .drop_duplicates()
        .reset_index(drop=True)
    )
    base["kunnr_s4"] = base["kunnr_ecc"].map(customers).fillna("")
    base["matnr_s4"] = base["matnr_ecc"].map(materials).fillna("")
    base["mapped"] = (base["kunnr_s4"] != "") & (base["matnr_s4"] != "")

    parts = []
    for offset, day in pricing_dates:
        part = base.copy()
        part["pricing_date"] = yyyymmdd(day)
        part["offset_days"] = offset
        parts.append(part)
    grain = pd.concat(parts, ignore_index=True)
    grain.insert(0, "row_id", range(len(grain)))
    return grain


def resolve_conditions(
    records: pd.DataFrame,
    grain: pd.DataFrame,
    kunnr_col: str,
    matnr_col: str,
    waterfall: WaterfallConfig,
) -> pd.DataFrame:
    base = grain[["row_id", "vkorg", "pricing_date", kunnr_col, matnr_col]].rename(
        columns={kunnr_col: "kunnr", matnr_col: "matnr"}
    )

    winners: list[pd.DataFrame] = []
    for cond in waterfall.cond_types:
        cond_records = records[records["kschl"] == cond]
        remaining = base
        for level in waterfall.access_sequences[cond]:
            if remaining.empty:
                break
            keys = ["vkorg", *LEVEL_KEYS[level]]
            level_records = cond_records[cond_records["level"] == level][keys + _PAYLOAD]
            if level_records.empty:
                continue
            candidates = remaining[["row_id", "pricing_date", *keys]].merge(
                level_records, on=keys, how="inner"
            )
            valid = candidates[
                (candidates["datab"] <= candidates["pricing_date"])
                & (candidates["datbi"] >= candidates["pricing_date"])
                & (candidates["scale_qty"] <= waterfall.reference_quantity)
            ]
            if valid.empty:
                continue
            # Highest scale tier wins; if validity ranges overlap, the latest start wins.
            best = valid.sort_values(["row_id", "scale_qty", "datab"]).drop_duplicates(
                "row_id", keep="last"
            )
            best = best.assign(kschl=cond, level=level, found=True)
            winners.append(best[["row_id", "kschl", "found", "level", *_PAYLOAD]])
            remaining = remaining[~remaining["row_id"].isin(best["row_id"])]

    every = base[["row_id"]].merge(pd.DataFrame({"kschl": list(waterfall.cond_types)}), how="cross")
    if winners:
        found = pd.concat(winners, ignore_index=True)
        out = every.merge(found, on=["row_id", "kschl"], how="left")
    else:
        out = every.copy()
        for column in _RESOLVED_COLUMNS[2:]:
            out[column] = None
    out["found"] = out["found"].eq(True)  # NaN (no record) becomes False
    return out[_RESOLVED_COLUMNS].sort_values(["row_id", "kschl"]).reset_index(drop=True)
