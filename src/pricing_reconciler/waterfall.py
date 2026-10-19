# flatten the pricing waterfall

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Mapping, Sequence

import pandas as pd

from .config import WaterfallConfig

# (amount, pricing unit) for a condition that was found; None when it was not found.
CondInput = tuple[Decimal, int] | None


def round_half_up(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def compute_waterfall(
    inputs: Mapping[str, CondInput], waterfall: WaterfallConfig, mode: str
) -> dict[str, Decimal | None]:
    exact: dict[str, Decimal] = {}
    shown: dict[str, Decimal] = {}
    decimals = waterfall.decimals

    for step in waterfall.procedure:
        if step.kind == "subtotal":
            value = sum((exact[ref] for ref in step.sum), Decimal(0))
            exact[step.id] = value
            shown[step.id] = round_half_up(value, decimals)
            continue

        entry = inputs.get(step.cond or "")
        if step.kind == "price":
            if entry is None:
                return {s.id: None for s in waterfall.procedure}
            value = entry[0] / Decimal(entry[1])
        elif entry is None:
            value = Decimal(0)
        elif step.kind == "percent":
            base = sum((exact[ref] for ref in step.base), Decimal(0))
            value = step.sign * entry[0] / Decimal(100) * base
        else:  # fixed amount per pricing unit
            value = step.sign * entry[0] / Decimal(entry[1])

        rounded = round_half_up(value, decimals)
        exact[step.id] = rounded if mode == "per_step" else value
        shown[step.id] = rounded

    return shown


def flatten(
    resolved: pd.DataFrame, row_ids: Sequence[int], waterfall: WaterfallConfig, mode: str
) -> pd.DataFrame:
    ids = list(row_ids)
    per_cond: dict[str, pd.DataFrame] = {}
    for cond in waterfall.cond_types:
        part = resolved[resolved["kschl"] == cond].set_index("row_id").reindex(ids)
        per_cond[cond] = part

    price_cond = waterfall.price_step.cond
    amounts = {c: list(p["kbetr"]) for c, p in per_cond.items()}
    units = {c: list(p["kpein"]) for c, p in per_cond.items()}
    found = {c: [bool(v) if v == v else False for v in p["found"]] for c, p in per_cond.items()}

    steps: dict[str, list[Decimal | None]] = {sid: [] for sid in waterfall.step_ids}
    for i in range(len(ids)):
        inputs: dict[str, CondInput] = {}
        for cond in waterfall.cond_types:
            inputs[cond] = (amounts[cond][i], int(units[cond][i])) if found[cond][i] else None
        result = compute_waterfall(inputs, waterfall, mode)
        for sid in waterfall.step_ids:
            steps[sid].append(result[sid])

    price = per_cond[price_cond]
    out = pd.DataFrame({"row_id": ids})
    for sid in waterfall.step_ids:
        out[sid] = pd.Series(steps[sid], dtype=object)
    out["currency"] = pd.Series(
        [c if f else "" for c, f in zip(price["konwa"], found[price_cond])], dtype=object
    )
    out["price_uom"] = pd.Series(
        [u if f else "" for u, f in zip(price["kmein"], found[price_cond])], dtype=object
    )
    return out
