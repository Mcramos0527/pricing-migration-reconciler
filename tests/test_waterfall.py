from decimal import Decimal

import pandas as pd

from pricing_reconciler.waterfall import compute_waterfall, flatten, round_half_up

D = Decimal


def test_round_half_up_is_commercial_rounding():
    # A binary float would give 2.67 here; SAP-style rounding gives 2.68.
    assert round_half_up(D("2.675"), 2) == D("2.68")
    assert round_half_up(D("-0.505"), 2) == D("-0.51")
    assert round_half_up(D("1.004"), 2) == D("1.00")


def test_full_waterfall_numbers(base_config):
    inputs = {
        "PR00": (D("100.00"), 1),
        "K007": (D("10"), 1),
        "ZREB": (D("2"), 1),
        "KF00": (D("1.50"), 1),
        "MWST": (D("21"), 1),
    }
    result = compute_waterfall(inputs, base_config.waterfall, "per_step")
    assert result["gross"] == D("100.00")
    assert result["discount"] == D("-10.00")
    assert result["rebate"] == D("-1.80")  # 2% of 90.00 (gross after discount)
    assert result["freight"] == D("1.50")
    assert result["net"] == D("89.70")
    assert result["tax"] == D("18.84")  # 21% of 89.70 = 18.837
    assert result["total"] == D("108.54")


def test_pricing_unit_divides_the_amount(base_config):
    result = compute_waterfall({"PR00": (D("150.00"), 100)}, base_config.waterfall, "per_step")
    assert result["gross"] == D("1.50")


def test_missing_gross_price_means_unpriced(base_config):
    result = compute_waterfall({"K007": (D("10"), 1)}, base_config.waterfall, "per_step")
    assert all(value is None for value in result.values())


def test_missing_optional_conditions_count_as_zero(base_config):
    result = compute_waterfall({"PR00": (D("50.00"), 1)}, base_config.waterfall, "per_step")
    assert result["discount"] == result["rebate"] == result["freight"] == result["tax"] == D("0.00")
    assert result["net"] == result["total"] == D("50.00")


def test_rounding_strategy_changes_the_cent(base_config):
    inputs = {"PR00": (D("10.10"), 1), "K007": (D("5"), 1), "ZREB": (D("5"), 1)}
    per_step = compute_waterfall(inputs, base_config.waterfall, "per_step")
    at_end = compute_waterfall(inputs, base_config.waterfall, "at_end")
    assert per_step["net"] == D("9.11")  # 10.10 - 0.51 - 0.48
    assert at_end["net"] == D("9.12")  # 10.10 - 0.505 - 0.47975 = 9.11525
    assert per_step["gross"] == at_end["gross"]


def test_flatten_builds_one_row_per_id_with_attributes(base_config):
    resolved = pd.DataFrame(
        [
            {
                "row_id": 0,
                "kschl": "PR00",
                "found": True,
                "level": "material",
                "kbetr": D("20.00"),
                "konwa": "EUR",
                "kpein": 1,
                "kmein": "EA",
                "scale_qty": 0,
                "datab": 20250101,
                "datbi": 99991231,
            },
            {
                "row_id": 1,
                "kschl": "PR00",
                "found": False,
                "level": None,
                "kbetr": None,
                "konwa": None,
                "kpein": None,
                "kmein": None,
                "scale_qty": None,
                "datab": None,
                "datbi": None,
            },
        ]
    )
    empty = dict.fromkeys(
        ["level", "kbetr", "konwa", "kpein", "kmein", "scale_qty", "datab", "datbi"], None
    )
    extra = [
        {"row_id": rid, "kschl": cond, "found": False, **empty}
        for cond in ("K007", "ZREB", "KF00", "MWST")
        for rid in (0, 1)
    ]
    resolved = pd.DataFrame([*resolved.to_dict("records"), *extra])
    flat = flatten(resolved, [0, 1], base_config.waterfall, "per_step")
    assert list(flat["row_id"]) == [0, 1]
    assert flat.loc[0, "net"] == D("20.00")
    assert flat.loc[0, "currency"] == "EUR" and flat.loc[0, "price_uom"] == "EA"
    assert flat.loc[1, "net"] is None
