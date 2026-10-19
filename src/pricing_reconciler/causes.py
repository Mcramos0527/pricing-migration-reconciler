
from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .compare import MISMATCH, PERFECT, ROUNDING
from .config import LEVEL_KEYS, WaterfallConfig


def _date_text(value: Any) -> str:
    number = int(value)
    return f"{number // 10000}-{number // 100 % 100:02d}-{number % 100:02d}"


def _record_text(
    level: Any, kbetr: Any, konwa: Any, kpein: Any, kmein: Any, scale: Any, datab: Any, datbi: Any
) -> str:
    amount = f"{kbetr} {konwa}"
    if int(kpein) != 1 or kmein:
        amount += f"/{int(kpein)} {kmein}".rstrip()
    scale_text = f", scale from {int(scale)}" if int(scale) > 0 else ""
    return f"{level} {amount}{scale_text}, valid {_date_text(datab)} to {_date_text(datbi)}"


def summarise_resolved(resolved: pd.DataFrame, row_ids: set[int]) -> dict[int, str]:
    found = resolved[resolved["found"] & resolved["row_id"].isin(row_ids)]
    parts: dict[int, list[str]] = {}
    for rec in found.itertuples(index=False):
        text = f"{rec.kschl}[{rec.level}] {rec.kbetr} {rec.konwa}"
        if int(rec.kpein) != 1 or rec.kmein:
            text += f"/{int(rec.kpein)} {rec.kmein}".rstrip()
        if int(rec.scale_qty) > 0:
            text += f" from {int(rec.scale_qty)}"
        parts.setdefault(int(rec.row_id), []).append(text)
    return {row_id: "; ".join(items) for row_id, items in parts.items()}


def _condition_findings(
    cond: str,
    flagged_ids: set[int],
    ecc_resolved: pd.DataFrame,
    s4_resolved: pd.DataFrame,
    s4_records: pd.DataFrame,
    grain: pd.DataFrame,
    waterfall: WaterfallConfig,
) -> pd.DataFrame:
    columns = ["found", "level", "kbetr", "konwa", "kpein", "kmein", "scale_qty", "datab", "datbi"]
    ecc = ecc_resolved[(ecc_resolved["kschl"] == cond) & ecc_resolved["row_id"].isin(flagged_ids)]
    s4 = s4_resolved[(s4_resolved["kschl"] == cond) & s4_resolved["row_id"].isin(flagged_ids)]
    merged = (
        ecc[["row_id", *columns]]
        .rename(columns={c: f"e_{c}" for c in columns})
        .merge(
            s4[["row_id", *columns]].rename(columns={c: f"s_{c}" for c in columns}),
            on="row_id",
            how="inner",
        )
        .merge(grain[["row_id", "vkorg", "kunnr_s4", "matnr_s4"]], on="row_id", how="left")
        .reset_index(drop=True)
    )
    if merged.empty:
        return pd.DataFrame(columns=["row_id", "kschl", "rule_id", "detail"])

    rank = {level: i for i, level in enumerate(waterfall.access_sequences[cond])}
    e_found = merged["e_found"].to_numpy(bool)
    s_found = merged["s_found"].to_numpy(bool)
    both = e_found & s_found
    e_only = e_found & ~s_found
    s_only = ~e_found & s_found
    e_rank = merged["e_level"].map(rank).to_numpy(float)
    s_rank = merged["s_level"].map(rank).to_numpy(float)

    # Does S/4 hold a record at ECC's key level at all, valid or not? Keep the closest one
    # (latest valid-to) so the detail text can show why it did not apply.
    exists = np.zeros(len(merged), dtype=bool)
    existing_text = np.full(len(merged), "", dtype=object)
    cond_records = s4_records[s4_records["kschl"] == cond]
    payload = ["kbetr", "konwa", "kpein", "kmein", "scale_qty", "datab", "datbi"]
    for level in merged.loc[e_found, "e_level"].unique():
        selected = (merged["e_level"] == level).to_numpy()
        keys = ["vkorg", *LEVEL_KEYS[level]]
        at_level = cond_records[cond_records["level"] == level]
        known = at_level.sort_values("datbi").drop_duplicates(keys, keep="last")[keys + payload]
        probe = merged.loc[selected, ["vkorg", "kunnr_s4", "matnr_s4"]].rename(
            columns={"kunnr_s4": "kunnr", "matnr_s4": "matnr"}
        )[keys]
        joined = probe.merge(known.assign(_hit=True), on=keys, how="left")
        hit = joined["_hit"].eq(True).to_numpy(bool)
        exists[selected] = hit
        existing_text[selected] = [
            (
                _record_text(
                    level, r.kbetr, r.konwa, r.kpein, r.kmein, r.scale_qty, r.datab, r.datbi
                )
                if found
                else ""
            )
            for r, found in zip(joined.itertuples(), hit)
        ]

    currency_diff = (
        both & (merged["e_konwa"].astype(str) != merged["s_konwa"].astype(str)).to_numpy()
    )
    unit_diff = (
        both
        & (
            (merged["e_kpein"] != merged["s_kpein"]) | (merged["e_kmein"] != merged["s_kmein"])
        ).to_numpy()
    )
    same_level = both & (merged["e_level"] == merged["s_level"]).to_numpy()
    scale_diff = same_level & (merged["e_scale_qty"] != merged["s_scale_qty"]).to_numpy()
    amount_diff = (
        same_level
        & ~scale_diff
        & np.array([a != b for a, b in zip(merged["e_kbetr"], merged["s_kbetr"])], dtype=bool)
    )
    validity_gap = (e_only | (both & (s_rank > e_rank))) & exists

    rule = np.full(len(merged), "", dtype=object)

    def assign(mask: np.ndarray, rule_id: str) -> None:
        rule[(rule == "") & mask] = rule_id

    # Order here is the priority order of the rules.
    assign(currency_diff, "R02")
    assign(unit_diff, "R03")
    assign(validity_gap, "R07")
    assign(e_only, "R04")
    assign(both & ~same_level, "R06")
    assign(scale_diff, "R08")
    assign(s_only, "R05")
    assign(amount_diff, "R09")

    rows = []
    for i in np.flatnonzero(rule != ""):
        rec = merged.iloc[i]
        ecc_text = (
            _record_text(
                rec.e_level,
                rec.e_kbetr,
                rec.e_konwa,
                rec.e_kpein,
                rec.e_kmein,
                rec.e_scale_qty,
                rec.e_datab,
                rec.e_datbi,
            )
            if e_found[i]
            else "none found"
        )
        s4_text = (
            _record_text(
                rec.s_level,
                rec.s_kbetr,
                rec.s_konwa,
                rec.s_kpein,
                rec.s_kmein,
                rec.s_scale_qty,
                rec.s_datab,
                rec.s_datbi,
            )
            if s_found[i]
            else "none found"
        )
        if rule[i] == "R07":
            expired = f"record {existing_text[i]} exists but is not valid on the pricing date"
            s4_text = f"{s4_text}; also {expired}" if s_found[i] else expired
        rows.append(
            {
                "row_id": int(rec.row_id),
                "kschl": cond,
                "rule_id": rule[i],
                "detail": f"{cond}: ECC {ecc_text} | S/4 {s4_text}",
            }
        )
    return pd.DataFrame(rows, columns=["row_id", "kschl", "rule_id", "detail"])


def analyse_causes(
    compared: pd.DataFrame,
    grain: pd.DataFrame,
    ecc_resolved: pd.DataFrame,
    s4_resolved: pd.DataFrame,
    s4_records: pd.DataFrame,
    waterfall: WaterfallConfig,
    rules: Mapping[str, Mapping[str, Any]],
    tolerance: Decimal,
) -> pd.DataFrame:
    flagged = compared[compared["status"] != PERFECT]
    columns = [
        "row_id",
        "rule_id",
        "cause_code",
        "probable_cause",
        "confidence",
        "cause_detail",
        "recommended_action",
        "all_cause_codes",
        "ecc_records",
        "s4_records",
    ]
    if flagged.empty:
        return pd.DataFrame(columns=columns)

    priority = {rid: int(info["priority"]) for rid, info in rules.items()}
    mapped_ids = set(flagged.loc[flagged["mapped"], "row_id"])
    mismatch_ids = set(flagged.loc[(flagged["status"] == MISMATCH) & flagged["mapped"], "row_id"])

    findings = pd.concat(
        [
            _condition_findings(
                c, mismatch_ids, ecc_resolved, s4_resolved, s4_records, grain, waterfall
            )
            for c in waterfall.cond_types
        ],
        ignore_index=True,
    )
    by_row = {int(k): v for k, v in findings.groupby("row_id")} if not findings.empty else {}

    ecc_text = summarise_resolved(ecc_resolved, mapped_ids)
    s4_text = summarise_resolved(s4_resolved, mapped_ids)

    out = []
    for rec in flagged.itertuples(index=False):
        row_id = int(rec.row_id)
        details: list[str] = []
        if not rec.mapped:
            codes = ["R01"]
            missing = []
            if not rec.kunnr_s4:
                missing.append(f"customer {rec.kunnr_ecc} has no business-partner mapping")
            if not rec.matnr_s4:
                missing.append(f"material {rec.matnr_ecc} has no 40-char material mapping")
            details = ["; ".join(missing)]
        elif rec.status == ROUNDING:
            codes = ["R10"]
            details = [f"largest step difference {rec.max_abs_delta} is within ±{tolerance}"]
        else:
            group = by_row.get(row_id)
            if group is None or group.empty:
                codes = ["R99"]
                details = [
                    f"max step difference {rec.max_abs_delta}; resolved conditions look the same"
                ]
            else:
                group = group.assign(_p=group["rule_id"].map(priority)).sort_values("_p")
                codes = list(dict.fromkeys(group["rule_id"]))
                details = [f"[{r}] {d}" for r, d in zip(group["rule_id"], group["detail"])]

        primary = codes[0]
        info = rules[primary]
        out.append(
            {
                "row_id": row_id,
                "rule_id": primary,
                "cause_code": info["code"],
                "probable_cause": info["title"],
                "confidence": info["confidence"],
                "cause_detail": " || ".join(details),
                "recommended_action": info["action"],
                "all_cause_codes": ", ".join(rules[c]["code"] for c in codes),
                "ecc_records": ecc_text.get(row_id, ""),
                "s4_records": s4_text.get(row_id, ""),
            }
        )
    return pd.DataFrame(out, columns=columns)
