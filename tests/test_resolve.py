import dataclasses
from datetime import date
from decimal import Decimal

import pandas as pd

from pricing_reconciler.extract import normalise_conditions
from pricing_reconciler.resolve import build_grain, resolve_conditions

PRICING_DAY = date(2026, 10, 18)


def records(*rows):
    columns = [
        "kschl",
        "vkorg",
        "kunnr",
        "matnr",
        "datab",
        "datbi",
        "kbetr",
        "konwa",
        "kpein",
        "kmein",
        "scale_qty",
    ]
    return normalise_conditions(pd.DataFrame(rows, columns=columns))


def row(
    kschl,
    kunnr,
    matnr,
    kbetr,
    *,
    datab=date(2026, 1, 1),
    datbi=date(9999, 12, 31),
    scale=0,
    vkorg="1000",
):
    return (kschl, vkorg, kunnr, matnr, datab, datbi, Decimal(kbetr), "EUR", 1, "EA", scale)


def grain_for(*pairs):
    scope = pd.DataFrame([{"vkorg": "1000", "kunnr": c, "matnr": m} for c, m in pairs])
    customers = pd.DataFrame({"kunnr_ecc": sorted({c for c, _ in pairs}), "partner_s4": ""})
    materials = pd.DataFrame({"matnr_ecc": sorted({m for _, m in pairs}), "matnr_s4": ""})
    customers["partner_s4"] = customers["kunnr_ecc"]
    materials["matnr_s4"] = materials["matnr_ecc"]
    return build_grain(scope, customers, materials, [(0, PRICING_DAY)])


def pick(resolved, row_id, cond="PR00"):
    return resolved[(resolved["row_id"] == row_id) & (resolved["kschl"] == cond)].iloc[0]


def test_most_specific_level_wins_and_others_fall_back(base_config):
    recs = records(
        row("PR00", "", "M1", "50.00"),
        row("PR00", "C1", "M1", "40.00"),
    )
    grain = grain_for(("C1", "M1"), ("C2", "M1"))
    resolved = resolve_conditions(recs, grain, "kunnr_ecc", "matnr_ecc", base_config.waterfall)

    specific = pick(resolved, 0)
    fallback = pick(resolved, 1)
    assert specific["level"] == "customer_material" and specific["kbetr"] == Decimal("40.00")
    assert fallback["level"] == "material" and fallback["kbetr"] == Decimal("50.00")


def test_condition_without_any_record_is_not_found(base_config):
    recs = records(row("PR00", "", "M1", "50.00"))
    resolved = resolve_conditions(
        recs, grain_for(("C1", "M1")), "kunnr_ecc", "matnr_ecc", base_config.waterfall
    )
    assert bool(pick(resolved, 0, "PR00")["found"]) is True
    assert bool(pick(resolved, 0, "K007")["found"]) is False


def test_scale_tier_follows_reference_quantity(base_config):
    recs = records(
        row("PR00", "", "M1", "50.00", scale=0),
        row("PR00", "", "M1", "45.00", scale=100),
        row("PR00", "", "M1", "40.00", scale=500),
    )
    grain = grain_for(("C1", "M1"))
    for quantity, expected in ((1, "50.00"), (100, "45.00"), (499, "45.00"), (500, "40.00")):
        waterfall = dataclasses.replace(base_config.waterfall, reference_quantity=quantity)
        resolved = resolve_conditions(recs, grain, "kunnr_ecc", "matnr_ecc", waterfall)
        assert pick(resolved, 0)["kbetr"] == Decimal(expected), quantity


def test_validity_window_is_respected(base_config):
    recs = records(
        row("PR00", "", "EXPIRED", "10.00", datab=date(2025, 1, 1), datbi=date(2026, 10, 17)),
        row("PR00", "", "FUTURE", "20.00", datab=date(2026, 10, 19)),
        row("PR00", "", "ENDS_TODAY", "30.00", datab=date(2026, 1, 1), datbi=PRICING_DAY),
    )
    grain = grain_for(("C1", "EXPIRED"), ("C1", "FUTURE"), ("C1", "ENDS_TODAY"))
    resolved = resolve_conditions(recs, grain, "kunnr_ecc", "matnr_ecc", base_config.waterfall)
    by_material = {
        grain.loc[grain["row_id"] == i, "matnr_ecc"].iloc[0]: bool(pick(resolved, i)["found"])
        for i in range(3)
    }
    assert by_material == {"EXPIRED": False, "FUTURE": False, "ENDS_TODAY": True}


def test_later_validity_start_wins_when_ranges_overlap(base_config):
    recs = records(
        row("PR00", "", "M1", "10.00", datab=date(2026, 1, 1)),
        row("PR00", "", "M1", "12.00", datab=date(2026, 6, 1)),
    )
    resolved = resolve_conditions(
        recs, grain_for(("C1", "M1")), "kunnr_ecc", "matnr_ecc", base_config.waterfall
    )
    assert pick(resolved, 0)["kbetr"] == Decimal("12.00")


def test_grain_flags_unmapped_keys_and_repeats_per_pricing_date():
    scope = pd.DataFrame(
        [
            {"vkorg": "1000", "kunnr": "C1", "matnr": "M1"},
            {"vkorg": "1000", "kunnr": "C2", "matnr": "M1"},
        ]
    )
    customers = pd.DataFrame({"kunnr_ecc": ["C1"], "partner_s4": ["BP1"]})
    materials = pd.DataFrame({"matnr_ecc": ["M1"], "matnr_s4": ["MAT-1"]})
    grain = build_grain(scope, customers, materials, [(0, PRICING_DAY), (30, date(2026, 11, 17))])
    assert len(grain) == 4
    assert sorted(grain["row_id"]) == [0, 1, 2, 3]
    unmapped = grain[grain["kunnr_ecc"] == "C2"]
    assert not unmapped["mapped"].any() and (unmapped["kunnr_s4"] == "").all()
    assert set(grain["pricing_date"]) == {20261018, 20261117}


def test_normalise_conditions_trims_padding_and_derives_level():
    frame = pd.DataFrame(
        [
            (
                "PR00 ",
                "1000",
                "0000000001",
                "M1   ",
                date(2026, 1, 1),
                "9999-12-31",
                1.5,
                "EUR ",
                1,
                "EA",
                0,
            ),
            (
                "K007",
                "1000",
                "0000000001",
                None,
                "2026-01-01",
                date(9999, 12, 31),
                "5.0",
                "%",
                1,
                None,
                0,
            ),
        ],
        columns=[
            "kschl",
            "vkorg",
            "kunnr",
            "matnr",
            "datab",
            "datbi",
            "kbetr",
            "konwa",
            "kpein",
            "kmein",
            "scale_qty",
        ],
    )
    out = normalise_conditions(frame)
    assert list(out["kschl"]) == ["PR00", "K007"]
    assert list(out["level"]) == ["customer_material", "customer"]
    assert list(out["datbi"]) == [99991231, 99991231]
    assert out.loc[0, "kbetr"] == Decimal("1.5") and out.loc[1, "kbetr"] == Decimal("5.0")
    assert out.loc[1, "matnr"] == "" and out.loc[1, "kmein"] == ""
