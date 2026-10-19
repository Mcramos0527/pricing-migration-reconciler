# compare the two flattened waterfalls and classify every row

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd

from .config import WaterfallConfig

PERFECT = "PERFECT"
ROUNDING = "ROUNDING"
MISMATCH = "MISMATCH"

_GRAIN_COLUMNS = [
    "row_id",
    "pricing_date",
    "offset_days",
    "vkorg",
    "kunnr_ecc",
    "kunnr_s4",
    "matnr_ecc",
    "matnr_s4",
    "mapped",
]


def _as_decimal(value: Any) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


def _delta(ecc: Decimal | None, s4: Decimal | None) -> Decimal | None:
    if ecc is None and s4 is None:
        return Decimal(0)  # unpriced on both sides: nothing differs
    if ecc is None or s4 is None:
        return None  # priced on one side only
    return s4 - ecc


def compare_waterfalls(
    grain: pd.DataFrame,
    ecc_flat: pd.DataFrame,
    s4_flat: pd.DataFrame,
    waterfall: WaterfallConfig,
    tolerance: Decimal,
) -> pd.DataFrame:
    ids = list(grain["row_id"])
    ecc = ecc_flat.set_index("row_id").reindex(ids)
    s4 = s4_flat.set_index("row_id").reindex(ids)  # unmapped rows are absent -> NaN

    out = grain[_GRAIN_COLUMNS].copy().reset_index(drop=True)
    deltas: dict[str, list[Decimal | None]] = {}
    for sid in waterfall.step_ids:
        e_values = [_as_decimal(v) for v in ecc[sid]]
        s_values = [_as_decimal(v) for v in s4[sid]]
        out[f"ecc_{sid}"] = pd.Series(e_values, dtype=object)
        out[f"s4_{sid}"] = pd.Series(s_values, dtype=object)
        deltas[sid] = [_delta(e, s) for e, s in zip(e_values, s_values)]
        out[f"delta_{sid}"] = pd.Series(deltas[sid], dtype=object)

    for column in ("currency", "price_uom"):
        short = "uom" if column == "price_uom" else column
        out[f"ecc_{short}"] = ecc[column].fillna("").astype(str).to_numpy()
        out[f"s4_{short}"] = s4[column].fillna("").astype(str).to_numpy()

    mapped = out["mapped"].to_numpy(bool)
    gross_id = waterfall.price_step.id
    gross_ecc = out[f"ecc_{gross_id}"].tolist()
    gross_s4 = out[f"s4_{gross_id}"].tolist()

    statuses: list[str] = []
    max_abs: list[Decimal | None] = []
    for i in range(len(out)):
        row_deltas = [deltas[sid][i] for sid in waterfall.step_ids]
        has_gap = any(d is None for d in row_deltas)
        largest = None if has_gap else max((abs(d) for d in row_deltas), default=Decimal(0))
        both_priced = gross_ecc[i] is not None and gross_s4[i] is not None
        attrs_differ = both_priced and (
            out.at[i, "ecc_currency"] != out.at[i, "s4_currency"]
            or out.at[i, "ecc_uom"] != out.at[i, "s4_uom"]
        )
        if not mapped[i] or has_gap or attrs_differ:
            status = MISMATCH
        elif largest == 0:
            status = PERFECT
        elif largest <= tolerance:
            status = ROUNDING
        else:
            status = MISMATCH
        statuses.append(status)
        max_abs.append(largest)

    out["status"] = statuses
    out["max_abs_delta"] = pd.Series(max_abs, dtype=object)
    return out
